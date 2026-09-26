#!/usr/bin/env python3
"""HF Job entrypoint: publish a BATCH of recitations to the HF dataset.

Reads ``SLUGS`` (a JSON array) and runs ``publish_hf.publish_slug`` for each
slug in its own SPAWNED subprocess, collecting a per-slug member result. The
isolation matters twice over: (1) each publish peaks at several GB (rows + audio
slices + the parquet Dataset), and the OS only reclaims that memory when the
child exits — so the footprint never accumulates across the batch and OOM-kills
the container; (2) spawn (not fork) starts a fresh interpreter so torch/datasets
import clean — forking after they are imported makes torchcodec encode + the
threaded slicer crawl (~45 min vs the ~68 s a clean top-level process takes).
Each slug's split is pushed independently — one slug failing (validation, audio,
push, OOM, timeout) never aborts the batch, it just lands as a ``failed``
member. After every slug is processed the dataset catalog + card are re-rendered
ONCE (one batch of pushes → one render).

The HF Job NEVER writes ``db/inspector.db``. On completion it:
  1. writes a durable batch record to
     ``jobs/_global/hf_publish_batch/<job_id>.json`` (the source of truth for
     Inspector's "Failed to publish" bucket — read by the poll fallback, which
     has no webhook payload), and
  2. POSTs ``/api/webhooks/hf-publish-batch-complete`` with the ``members``
     array. ``services.admin.jobs.hf_publish_batch.complete()`` inserts one
     ``per_recitation_releases(track='hf')`` row per succeeded member (reusing
     the single-publish ``hf_publish.complete``) and records the failures.

Env:
  SLUGS                     (required) JSON array of reciter slugs
  INSPECTOR_BUCKET_MOUNT    bucket mount root (default ``/data``)
  JOB_ID                    HF-injected job id (forwarded in callback)
  HF_TOKEN                  HF auth (secret)
  INSPECTOR_WEBHOOK_URL     (optional) Inspector completion endpoint
  INSPECTOR_WEBHOOK_SECRET  (optional) HMAC shared secret
  LAUNCHED_BY               (optional) hf_user_id of the operator who clicked
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_jobs.publish_hf import (  # noqa: E402
    _bucket_root,
    _resolve_dataset_repo_id,
    _sync_dataset_catalog_and_card,
    publish_slug,
)
from qua_shared.inspector_notify import WEBHOOK_USER_AGENT  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("publish_hf_batch")

BATCH_RECORD_REL = "jobs/_global/hf_publish_batch"


def _parse_slugs() -> list[str]:
    raw = os.environ.get("SLUGS", "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Tolerate a bare comma-separated list as a fallback.
        parsed = [s.strip() for s in raw.split(",")]
    return [str(s).strip() for s in parsed if str(s).strip()]


def _member(result: dict) -> dict:
    """Project a ``publish_slug`` result to the wire/record member shape."""
    return {
        "slug": result["slug"],
        "status": result["status"],
        "version": result.get("version") or "",
        "external_uri": result.get("external_uri") or "",
        "validation_summary": result.get("validation_summary"),
        "error": result.get("error"),
    }


def _write_batch_record(job_id: str, members: list[dict], completed_at: str) -> None:
    """Persist the batch outcome to the bucket so the Inspector poll fallback
    (no webhook payload) can reconcile failures. Best-effort."""
    path = _bucket_root() / BATCH_RECORD_REL / f"{job_id}.json"
    payload = {
        "job_id": job_id,
        "kind": "hf_publish_batch",
        "completed_at": completed_at,
        "members": members,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        log.info("wrote batch record %s", path)
    except OSError as exc:
        log.warning("failed to write batch record %s: %s", path, exc)


def _post_webhook(*, job_id: str, members: list[dict], launched_by: str | None) -> bool:
    """POST the batch completion webhook. Returns True on 2xx; False otherwise
    (the 120 s poll worker is the safety net, reading the bucket record)."""
    url = os.environ.get("INSPECTOR_WEBHOOK_URL", "").strip()
    secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        log.info("webhook URL/secret unset — skipping callback (poll fallback applies)")
        return False
    body = {
        "kind": "hf_publish_batch",
        "job_id": job_id,
        "status": "succeeded",
        "members": members,
        "launched_by": launched_by,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Inspector-Job-Secret": secret,
            "User-Agent": WEBHOOK_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            log.info("webhook %s → %s", url, resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("webhook POST failed: %s", exc)
        return False


def _publish_worker(slug: str, job_id: str, out_path: str) -> None:
    """Child-process entry: publish one slug, write its result dict as JSON.

    Runs in a SPAWNED subprocess so the OS reclaims ALL of this reciter's memory
    (rows + audio slices + the parquet Dataset, several GB peak) on exit —
    ``gc.collect()`` alone leaves the freed pages in the process RSS, which
    accumulated across reciters and OOM-killed the container. spawn (not fork)
    also gives a clean interpreter so torch/datasets import fresh per reciter.
    """
    pad_start = int(os.environ.get("PUBLISH_PAD_START", "").strip() or 100)
    pad_end = int(os.environ.get("PUBLISH_PAD_END", "").strip() or 300)
    min_gap = int(os.environ.get("PUBLISH_MIN_GAP", "").strip() or 100)
    try:
        res = publish_slug(
            slug, job_id, sync_card=False, pad_start=pad_start, pad_end=pad_end, min_gap=min_gap
        )
    except Exception as exc:
        res = {"slug": slug, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    try:
        Path(out_path).write_text(json.dumps(res), encoding="utf-8")
    except OSError:
        pass


def _publish_isolated(slug: str, job_id: str, ctx, timeout_s: int) -> dict:
    """Publish ``slug`` in a child process; return its result dict.

    A subprocess that OOM-kills (exitcode -9), crashes, times out, or writes no
    result is recorded as a failed member — the batch never aborts on one slug.
    """
    fd, out_path = tempfile.mkstemp(prefix=f"pub_{slug}_", suffix=".json")
    os.close(fd)
    try:
        p = ctx.Process(target=_publish_worker, args=(slug, job_id, out_path))
        p.start()
        p.join(timeout=timeout_s)
        if p.is_alive():
            p.terminate()
            p.join(30)
            return {"slug": slug, "status": "failed", "error": f"timeout after {timeout_s}s"}
        raw = ""
        try:
            raw = Path(out_path).read_text(encoding="utf-8")
        except OSError:
            raw = ""
        if not raw:
            ec = p.exitcode
            reason = "OOMKilled" if ec == -9 else f"subprocess exited {ec} with no result"
            log.error("  %s: %s", slug, reason)
            return {"slug": slug, "status": "failed", "error": reason}
        return json.loads(raw)
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip() or "unknown"
    slugs = _parse_slugs()
    log.info("publish_hf_batch: job=%s slugs=%s", job_id, slugs)
    if not slugs:
        log.error("SLUGS env var is required (JSON array)")
        return 2

    # SPAWN, not fork: each reciter publish runs torchcodec encoding + a threaded
    # slicer. Forking AFTER torch/datasets are imported (their OpenMP + internal
    # thread pools already live) makes that work in the child crawl (~45 min vs
    # the ~68 s a clean top-level process takes — fork-after-threads is undefined).
    # spawn starts a fresh interpreter so torch imports clean in the child; memory
    # is still fully reclaimed on child exit (kills the accumulation OOM).
    ctx = mp.get_context("spawn")
    per_reciter_timeout = int(os.environ.get("INSPECTOR_BATCH_RECITER_TIMEOUT_S", "2700"))

    members: list[dict] = []
    any_succeeded = False
    for i, slug in enumerate(slugs, 1):
        log.info("=== [%d/%d] publishing %s (isolated) ===", i, len(slugs), slug)
        result = _publish_isolated(slug, job_id, ctx, per_reciter_timeout)
        members.append(_member(result))
        if result.get("status") == "succeeded":
            any_succeeded = True

    # Re-render the dataset catalog + card ONCE after all splits are pushed.
    if any_succeeded:
        try:
            _sync_dataset_catalog_and_card(_resolve_dataset_repo_id())
        except Exception as exc:
            log.warning("dataset card/catalog sync failed: %s", exc)

    from datetime import UTC, datetime

    completed_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_batch_record(job_id, members, completed_at)
    _post_webhook(job_id=job_id, members=members, launched_by=os.environ.get("LAUNCHED_BY"))

    published = sum(1 for m in members if m["status"] == "succeeded")
    failed = len(members) - published
    log.info("publish_hf_batch: done — %d published, %d failed", published, failed)
    # The JOB itself succeeded as long as it ran to completion; per-member
    # failures are carried in the record/webhook, not the process exit code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
