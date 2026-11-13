"""HF dataset publish kind — BATCH push (one job, many recitations).

Launches a single HF Job that runs ``qua_jobs/publish_hf_batch.py`` over a list
of slugs. The job publishes each slug's split independently (one slug failing
never aborts the batch) and reports a per-slug ``members`` array — succeeded
members carry the HF revision SHA, failed members carry an error string.

On completion the batch webhook → ``complete()`` inserts one
``per_recitation_releases(track='hf', ...)`` row per *succeeded* member (reusing
``hf_publish.complete``, idempotent) and leaves the failures to surface via the
durable batch record at ``jobs/_global/hf_publish_batch/<job_id>.json``. That
record (written by the job) is the source of truth for the Releases-tab
"Failed to publish" bucket + summary banner; the poll fallback reads it when no
webhook payload is available.

The HF Job NEVER writes the DB.
"""

from __future__ import annotations

import json
import logging
import os

from . import base, hf_publish, records

log = logging.getLogger("inspector")

KIND = "hf_publish_batch"
BATCH_LABEL_SLUG = "_batch"

# Each recitation is published in its own child process (see publish_hf_batch),
# so the per-reciter PEAK (not the sum) is the constraint — isolation reclaims
# each reciter's memory before the next. The peak scales with audio size, and
# the parquet push of the largest reciter (~43h / ~2.5 GB audio) crosses
# cpu-basic's 16 GB, so the batch runs on cpu-upgrade (32 GB). A single child
# OOM trips a cgroup OOM that fails the whole job, so headroom must cover the
# biggest reciter, not the average.
JOB_FLAVOR = os.environ.get("INSPECTOR_HF_BATCH_JOB_FLAVOR", "cpu-upgrade")
JOB_TIMEOUT = os.environ.get("INSPECTOR_HF_BATCH_JOB_TIMEOUT", "3h")


def launch(slugs: list[str], *, settings=None, webhook_base: str | None = None) -> dict:
    """Launch one batch publish job for ``slugs``. Returns ``{job_id, url}``.

    ``settings`` is an optional ``ReleaseSettings`` (clip-edge pads); its
    fields are threaded to the job as ``PUBLISH_PAD_START`` / ``PUBLISH_PAD_END``
    / ``PUBLISH_MIN_GAP`` and read per-slug in ``publish_hf_batch``.
    """
    from typing import cast

    from huggingface_hub import SpaceHardware, Volume, get_token, run_job

    from services.storage.hf_bucket import resolve_bucket_repo

    if not slugs:
        raise RuntimeError("no slugs to publish")
    # Global single-flight: only one batch at a time.
    busy = base.running_job_for(kind=KIND)
    if busy is not None:
        raise RuntimeError(f"a batch publish is already in flight: id={busy[1]}")

    base.stage_job_code()
    bucket = resolve_bucket_repo()
    env = {
        "SLUGS": json.dumps(slugs),
        "INSPECTOR_BUCKET_MOUNT": "/data",
        "PYTHONPATH": "/aux/code",
    }
    if settings is not None:
        env["PUBLISH_PAD_START"] = str(settings.pad_start)
        env["PUBLISH_PAD_END"] = str(settings.pad_end)
        env["PUBLISH_MIN_GAP"] = str(settings.min_gap)
    secrets = {"HF_TOKEN": get_token()}
    webhook_secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if webhook_secret and webhook_base:
        env["INSPECTOR_WEBHOOK_URL"] = (
            webhook_base.rstrip("/") + "/api/webhooks/hf-publish-batch-complete"
        )
        secrets["INSPECTOR_WEBHOOK_SECRET"] = webhook_secret

    deps = "datasets orjson pyyaml torch torchcodec"
    entrypoint = "python /aux/code/qua_jobs/publish_hf_batch.py"
    command = base.job_command(entrypoint, deps)

    job = run_job(
        image=base.JOB_IMAGE,
        command=command,
        flavor=cast(SpaceHardware, JOB_FLAVOR),
        timeout=JOB_TIMEOUT,
        env=env,
        secrets=secrets,
        volumes=[
            Volume(type="bucket", source=bucket, mount_path="/data"),
            Volume(type="bucket", source=base.ALIGNER_BUCKET, mount_path="/aux", read_only=True),
        ],
        labels={"task": KIND, "reciter": BATCH_LABEL_SLUG},
    )
    job_id = base.hf_job_id(job) or ""
    url = getattr(job, "url", None)
    records.record_launch(KIND, None, job_id, url=url)
    log.info("launched hf_publish_batch job %s for %d slugs", job_id, len(slugs))
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    return {"job_id": job_id, "url": url}


def _members_from_record(job_id: str) -> list[dict]:
    """Read the durable batch record the job wrote to the bucket (poll path —
    no webhook payload). Empty list if the record is missing/unreadable."""
    raw = base.read_record_bytes(KIND, None, job_id)
    if raw is None:
        return []
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    members = rec.get("members")
    return members if isinstance(members, list) else []


def complete(
    job_id: str,
    *,
    members: list[dict] | None = None,
    launched_by: str | None = None,
) -> dict:
    """Record a batch publish outcome. Idempotent (delegates to
    ``hf_publish.complete`` per member, which keys on (track, slug, version)).

    ``members`` comes from the webhook payload; when called from the poll
    fallback (``members=None``) it is read from the durable bucket record.
    Succeeded members insert an ``hf`` release row; failed members are left to
    surface from the bucket record via the status endpoint. Returns a small
    summary ``{published, failed}``.
    """
    if members is None:
        members = _members_from_record(job_id)

    published = 0
    failed = 0
    for m in members:
        slug = (m.get("slug") or "").strip()
        if not slug:
            continue
        if m.get("status") == "succeeded":
            try:
                hf_publish.complete(
                    slug,
                    job_id,
                    version=m.get("version") or None,
                    external_uri=m.get("external_uri") or None,
                    launched_by=launched_by,
                    validation_summary=m.get("validation_summary"),
                )
                published += 1
            except Exception as exc:
                log.warning("batch member complete(%s) failed: %s", slug, exc)
                failed += 1
        else:
            failed += 1
    slim_members = [
        {
            "slug": (m.get("slug") or "").strip() or None,
            "status": "succeeded" if m.get("status") == "succeeded" else "failed",
            "version": m.get("version") or None,
            "external_uri": m.get("external_uri") or None,
            "error": m.get("error") or None,
        }
        for m in members
        if (m.get("slug") or "").strip()
    ]
    records.record_terminal(
        KIND,
        None,
        job_id,
        status="succeeded",
        members=slim_members,
        launched_by=launched_by,
    )
    log.info("hf_publish_batch.complete(%s): %d published, %d failed", job_id, published, failed)
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    return {"ok": True, "published": published, "failed": failed}


#: Batch records read to find the newest — job ids open with their creation time
#: (HF ObjectIds) and batches are single-flight, so the newest few hold the answer.
_RECENT_BATCHES = 3


def latest_batch_outcome() -> dict | None:
    """Return the most-recent batch record ``{job_id, completed_at, members}``
    from ``jobs/_global/hf_publish_batch/``, or None when there's never been a
    batch. Drives the Releases-tab "Failed to publish" bucket + summary banner.

    Only the :data:`_RECENT_BATCHES` newest-created records are read (every one
    is a bucket read, and the status grid polls this); among them the newest is
    chosen by ``completed_at`` (ISO-8601, lexicographically sortable), falling
    back to the job id when absent."""
    from services.storage.hf_bucket import get_backend

    backend = get_backend()
    try:
        names = backend.list_dir("jobs/_global/hf_publish_batch")
    except Exception:
        return None
    job_ids = sorted(name[: -len(".json")] for name in names if name.endswith(".json"))
    records: list[dict] = []
    for job_id in job_ids[-_RECENT_BATCHES:]:
        raw = base.read_record_bytes(KIND, None, job_id)
        if raw is None:
            continue
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        rec.setdefault("job_id", job_id)
        records.append(rec)
    if not records:
        return None
    records.sort(key=lambda r: (r.get("completed_at") or "", r.get("job_id") or ""))
    return records[-1]


def register() -> None:
    # Poll fallback: no members payload — complete() reads the bucket record.
    def _handler(_slug: str | None, jid: str) -> None:
        complete(jid)

    base.register_handler(KIND, _handler)
