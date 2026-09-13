"""Public surface of the native align pipeline: start / retry / cancel / status.

A run is one ``align_runs`` row plus a worker thread (``runner``). Single-flight
per slug is the partial unique index on the table: a pending / running / failed
run blocks a second start until it is retried to completion or canceled.
"""

from __future__ import annotations

import logging

from qua_shared.riwayat import DEFAULT_RIWAYAH, UnsupportedRiwayah, resolve_sdk_slug
from qua_shared.schemas import Actor, AlignRunStatus, ReciterState
from services.audio import audio_meta
from services.db import repo_align_runs, repo_catalog
from services.db.sync import durable_transaction
from services.state import state as state_service
from services.storage import cache
from utils.uuid7 import uuid7

from . import params as _params
from . import progress, stage_acquire, staging
from .params import AlignParams

log = logging.getLogger("inspector")

STARTABLE_STATES = (ReciterState.CATALOGUED, ReciterState.AWAITING_ALIGNMENT)


class AlignRunError(Exception):
    """Refused start/retry/cancel. ``status`` is the HTTP code the route maps to."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def start(
    slug: str,
    actor: Actor,
    *,
    model_name: str = _params.MODEL_LARGE,
    device: str = _params.DEVICE_GPU,
) -> AlignRunStatus:
    missing = _params.missing_config()
    if missing:
        raise AlignRunError(f"align pipeline not configured: {', '.join(missing)} unset", 503)
    if repo_align_runs.active_for_slug(slug) is not None:
        raise AlignRunError(f"{slug} already has an active align run", 409)
    delivery = repo_catalog.find_delivery(slug)
    if delivery is None:
        raise AlignRunError(f"{slug}: no delivery in the catalog", 404)
    row = state_service.get_row(slug)
    if row is None or row.state not in STARTABLE_STATES:
        state = row.state.value if row else "none"
        raise AlignRunError(f"{slug}: state {state} is not awaiting alignment", 409)
    chapters = audio_meta.chapter_numbers(slug)
    if not chapters:
        raise AlignRunError(f"{slug}: audio manifest lists no chapters", 400)
    if delivery.audio_category != "by_surah":
        raise AlignRunError(f"{slug}: only by_surah deliveries are supported", 400)
    try:
        riwayah = resolve_sdk_slug(delivery.riwayah or DEFAULT_RIWAYAH)
    except UnsupportedRiwayah as exc:
        raise AlignRunError(f"{slug}: {exc}", 400) from exc

    params = AlignParams(model_name=model_name, riwayah=riwayah, device=device)
    run_id = uuid7()
    with durable_transaction():
        repo_align_runs.insert(
            run_id=run_id,
            slug=slug,
            requested_by=actor.hf_user_id,
            params_json=params.to_json(),
            chapters_total=len(chapters),
        )
    cache.invalidate_admin_requests_cache()
    log.info(
        "align: %s started run %s for %s (%d chapters, %s lane)",
        actor.hf_user_id,
        run_id,
        slug,
        len(chapters),
        device,
    )
    from . import runner

    runner.ensure_worker(repo_align_runs.get(run_id))
    return _status_required(slug)


def _status_required(slug: str) -> AlignRunStatus:
    status = status_for_slug(slug)
    if status is None:  # pragma: no cover — the row was just written
        raise AlignRunError(f"no align run for {slug}", 500)
    return status


def retry(slug: str, actor: Actor) -> AlignRunStatus:
    run = repo_align_runs.active_for_slug(slug)
    if run is None:
        raise AlignRunError(f"{slug}: no active align run", 404)
    if run["status"] != "failed":
        raise AlignRunError(
            f"{slug}: run is {run['status']}, only a failed run can be retried", 409
        )
    with durable_transaction():
        repo_align_runs.update(run["run_id"], status="pending", attempt=run["attempt"] + 1)
    cache.invalidate_admin_requests_cache()
    log.info(
        "align: %s retried run %s (attempt %d)", actor.hf_user_id, run["run_id"], run["attempt"] + 1
    )
    from . import runner

    runner.ensure_worker(repo_align_runs.get(run["run_id"]))
    return _status_required(slug)


def cancel(slug: str, actor: Actor) -> AlignRunStatus:
    run = repo_align_runs.active_for_slug(slug)
    if run is None:
        raise AlignRunError(f"{slug}: no active align run", 404)
    progress.request_cancel(run["run_id"])
    if run["stage"] == "acquire" and run.get("acquire_job_id"):
        stage_acquire.cancel(run["acquire_job_id"])
    from . import runner

    if run["status"] == "failed" or not runner.is_alive(run["run_id"]):
        # Nothing is executing: finalise here instead of waiting for a worker.
        with durable_transaction():
            repo_align_runs.update(run["run_id"], status="canceled")
        progress.clear_cancel(run["run_id"])
        progress.clear_detail(run["run_id"])
        cache.invalidate_admin_requests_cache()
    log.info("align: %s canceled run %s", actor.hf_user_id, run["run_id"])
    return _status_required(slug)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status_for_slug(slug: str) -> AlignRunStatus | None:
    run = repo_align_runs.active_for_slug(slug) or repo_align_runs.latest_for_slug(slug)
    return to_status(run) if run else None


def active_status_by_slug() -> dict[str, AlignRunStatus]:
    """Every active run, keyed by slug — the Requests-tab overlay."""
    return {run["slug"]: to_status(run) for run in repo_align_runs.list_active()}


def to_status(run: dict) -> AlignRunStatus:
    detail = progress.get_detail(run["run_id"])
    params = AlignParams.from_json(run.get("params_json"))
    chapters_done = int(detail.pop("chapters_done", 0) or 0)
    if run["stage"] in ("sidecars", "assemble", "done"):
        chapters_done = int(run.get("chapters_total") or 0)
    job_id = run.get("acquire_job_id") or detail.get("job_id")
    from services.admin.jobs import base as _jobs_base

    return AlignRunStatus(
        run_id=run["run_id"],
        slug=run["slug"],
        stage=run["stage"],
        status=run["status"],
        attempt=int(run.get("attempt") or 1),
        requested_by=run.get("requested_by"),
        model_name=params.model_name,
        device=params.device,
        chapters_total=int(run.get("chapters_total") or 0),
        chapters_done=chapters_done,
        chapter_failures=list(detail.pop("chapter_failures", []) or []),
        detail=detail,
        acquire_job_id=job_id,
        acquire_job_url=_jobs_base.hf_job_url(job_id) if job_id else None,
        last_error=run.get("last_error"),
        started_at=run["started_at"],
        updated_at=run["updated_at"],
        ended_at=run.get("ended_at"),
    )


def staged_progress(run: dict) -> int:
    """Chapters already staged for a run — rebuilt from the bucket after a restart."""
    return len(staging.staged_chapters(run["slug"], run["run_id"]))
