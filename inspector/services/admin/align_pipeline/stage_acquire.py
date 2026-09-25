"""Stage 1 — persist source audio + peaks to the bucket via a CPU HF Job.

Launches ``qua_jobs/acquire_audio.py`` with the same volumes/labels shape as the
other job kinds (bucket RW at ``/data``, aligner-bucket code RO at ``/aux``), then
polls it to a terminal state from the worker thread. The job's report at
``staging/<slug>/<run>/acquire.json`` is the stage's output; any source failure
fails the stage with the failure list so the source can be fixed and retried.

The launch/poll helpers are shared with the split stage (``split_audio``).
YouTube sources get yt-dlp + deno (its JS challenge solver) and, when the Space
has them, the ``INSPECTOR_YTDLP_COOKIES`` / ``INSPECTOR_YTDLP_PROXY`` secrets —
YouTube refuses Hugging Face IPs without a signed-in session.
"""

from __future__ import annotations

import logging
import os
import time
from typing import cast

from qua_shared.audio.sources import SourceGroup, needs_ytdlp
from services.admin.jobs import base, records
from services.storage.hf_bucket import resolve_bucket_repo

from . import progress, staging

log = logging.getLogger("inspector")

KIND = "acquire_audio"
JOB_FLAVOR = os.environ.get("INSPECTOR_ACQUIRE_JOB_FLAVOR", "cpu-upgrade")
JOB_TIMEOUT = os.environ.get("INSPECTOR_ACQUIRE_JOB_TIMEOUT", "6h")
POLL_INTERVAL_S = 30
_ENTRYPOINT = "python /aux/code/qua_jobs/acquire_audio.py"
_YTDLP_DEPS = "yt-dlp[default] deno"


class AcquireError(RuntimeError):
    pass


def launch(slug: str, run_id: str, *, groups: list[SourceGroup], channels: int | None) -> str:
    """Launch the acquire job; returns its HF job id."""
    ytdlp = any(needs_ytdlp(g.url) for g in groups)
    env = {"CHANNELS": str(channels)} if channels in (1, 2) else {}
    return launch_job(
        KIND,
        _ENTRYPOINT,
        slug,
        run_id,
        deps="numpy" + (f" {_YTDLP_DEPS}" if ytdlp else ""),
        env=env,
        secrets=_ytdlp_secrets() if ytdlp else {},
    )


def _ytdlp_secrets() -> dict[str, str]:
    out = {}
    for env_name, job_name in (
        ("INSPECTOR_YTDLP_COOKIES", "YTDLP_COOKIES"),
        ("INSPECTOR_YTDLP_PROXY", "YTDLP_PROXY"),
    ):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            out[job_name] = value
    return out


def launch_job(
    kind: str,
    entrypoint: str,
    slug: str,
    run_id: str,
    *,
    deps: str,
    env: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
) -> str:
    from huggingface_hub import SpaceHardware, Volume, get_token, run_job

    busy = base.running_job_for(slug=slug)
    if busy is not None:
        raise AcquireError(f"job already in flight for {slug}: kind={busy[0]} id={busy[1]}")

    base.stage_job_code()
    job_env = {
        "SLUG": slug,
        "RUN_ID": run_id,
        "INSPECTOR_BUCKET_MOUNT": "/data",
        "PYTHONPATH": "/aux/code",
        **(env or {}),
    }
    job = run_job(
        image=base.JOB_IMAGE,
        command=base.job_command(entrypoint, deps),
        flavor=cast(SpaceHardware, JOB_FLAVOR),
        timeout=JOB_TIMEOUT,
        env=job_env,
        secrets={"HF_TOKEN": get_token(), **(secrets or {})},
        volumes=[
            Volume(type="bucket", source=resolve_bucket_repo(), mount_path="/data"),
            Volume(type="bucket", source=base.ALIGNER_BUCKET, mount_path="/aux", read_only=True),
        ],
        labels={"task": kind, "reciter": slug},
    )
    job_id = base.hf_job_id(job) or ""
    records.record_launch(kind, slug, job_id, url=getattr(job, "url", None))
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    log.info("align %s: launched %s job %s for %s", run_id, kind, job_id, slug)
    return job_id


def wait(slug: str, run_id: str, job_id: str) -> dict:
    """Block until the acquire job is terminal; return its report. Raises on failure."""
    return wait_job(KIND, slug, run_id, job_id, staging.acquire_path(slug, run_id))


def wait_job(kind: str, slug: str, run_id: str, job_id: str, report_path: str) -> dict:
    from huggingface_hub import inspect_job

    while True:
        progress.check_cancel(run_id)
        try:
            status = base.hf_status_str(inspect_job(job_id=job_id))
        except Exception as exc:  # noqa: BLE001 — transient HF API errors
            log.warning("align %s: inspect_job(%s) failed: %s", run_id, job_id, exc)
            status = "unknown"
        progress.set_detail(run_id, job_id=job_id, job_status=status)
        if status in base.TERMINAL:
            break
        time.sleep(POLL_INTERVAL_S)

    report = staging.read_json(report_path) or {}
    failures = report.get("failures") or {}
    ok = status in base.TERMINAL_SUCCESS and not failures
    records.record_terminal(kind, slug, job_id, status="succeeded" if ok else "failed")
    if not ok:
        detail = "; ".join(f"{k}: {err}" for k, err in sorted(failures.items(), key=_key))
        raise AcquireError(
            f"{kind} job {job_id} ended {status}"
            + (f" — {len(failures)} failed: {detail}" if failures else "")
        )
    return report


def cancel(job_id: str) -> None:
    from huggingface_hub import cancel_job

    try:
        cancel_job(job_id=job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("align: cancel_job(%s) failed: %s", job_id, exc)


def _key(item: tuple[str, str]) -> int:
    digits = "".join(c for c in item[0] if c.isdigit())
    return int(digits) if digits else 0
