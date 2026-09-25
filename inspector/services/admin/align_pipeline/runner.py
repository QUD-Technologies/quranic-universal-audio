"""Worker threads that drive an align run through its stages.

One daemon thread per active run. ``resume_active()`` at boot restarts every
pending/running row (a ``running`` row after a restart means the previous
process died mid-stage); a ``failed`` row waits for an explicit retry. Every
stage is resumable from its staged files, so a restarted worker skips what the
previous one finished.

Stage transitions are the only DB writes (``durable_transaction`` pushes the
whole database to the bucket each time); per-chapter progress is in
``progress`` + the staged files.
"""

from __future__ import annotations

import logging
import threading

from services.db import repo_align_runs
from services.db.sync import durable_transaction
from services.storage import cache

from . import (
    manifest,
    progress,
    sources,
    stage_acquire,
    stage_align,
    stage_assemble,
    stage_sidecars,
    stage_split,
    staging,
)
from .params import AlignParams
from .sources import SourceGroup

log = logging.getLogger("inspector")

STAGES = ("acquire", "align", "sidecars", "assemble")

_lock = threading.Lock()
_workers: dict[str, threading.Thread] = {}


def ensure_worker(run: dict | None) -> None:
    """Start a worker for ``run`` unless one is already alive."""
    if run is None:
        return
    run_id = run["run_id"]
    with _lock:
        existing = _workers.get(run_id)
        if existing is not None and existing.is_alive():
            return
        t = threading.Thread(target=_drive, args=(run_id,), name=f"align-{run_id[:8]}", daemon=True)
        _workers[run_id] = t
        t.start()


def is_alive(run_id: str) -> bool:
    with _lock:
        t = _workers.get(run_id)
    return t is not None and t.is_alive()


def resume_active() -> int:
    """Boot: restart every pending/running run. Returns how many were started."""
    started = 0
    for run in repo_align_runs.list_active():
        if run["status"] in ("pending", "running"):
            ensure_worker(run)
            started += 1
    if started:
        log.info("align: resumed %d run(s)", started)
    return started


# ---------------------------------------------------------------------------
# The drive loop
# ---------------------------------------------------------------------------


def _drive(run_id: str) -> None:
    run = repo_align_runs.get(run_id)
    if run is None:
        return
    slug = run["slug"]
    params = AlignParams.from_json(run.get("params_json"))
    try:
        groups = _groups(slug, run_id)
        stage = run["stage"]
        for name in STAGES[STAGES.index(stage) :]:
            progress.check_cancel(run_id)
            _mark(run_id, stage=name, status="running", last_error=None)
            run = repo_align_runs.get(run_id) or run
            if name == "acquire":
                _acquire(run, groups)
            elif name == "align":
                stage_align.run(slug, run_id, params, groups)
                stage_split.run(slug, run_id, groups)
            elif name == "sidecars":
                chapters, sources = _chapters(slug)
                stage_sidecars.run(slug, run_id, params, chapters, sources)
            else:
                chapters, sources = _chapters(slug)
                stage_assemble.run(
                    slug, run_id, params, chapters, sources, started_at=run["started_at"]
                )
        _mark(run_id, stage="done", status="succeeded")
        progress.clear_detail(run_id)
        log.info("align %s: done (%s)", run_id, slug)
        _reconcile()
    except progress.Canceled:
        _mark(run_id, status="canceled")
        progress.clear_detail(run_id)
        log.info("align %s: canceled (%s)", run_id, slug)
    except Exception as exc:  # noqa: BLE001 — every failure lands on the row
        log.exception("align %s: failed at stage %s", run_id, run["stage"])
        _mark(run_id, status="failed", last_error=f"{type(exc).__name__}: {exc}"[:2000])
    finally:
        progress.clear_cancel(run_id)


def _groups(slug: str, run_id: str) -> list[SourceGroup]:
    """The run's source groups, frozen at its first start.

    Split rewrites the manifest (drops, adoptions), which could renumber the
    combined-file slots; freezing the grouping keeps a resumed run pointing at
    the slot files and staged results it made."""
    path = staging.run_file(slug, run_id, staging.GROUPS_FILE)
    frozen = staging.read_json(path)
    if frozen is not None:
        return [
            SourceGroup(url=g["url"], chapters=tuple(g["chapters"]), slot=g.get("slot"))
            for g in frozen["groups"]
        ]
    groups = sources.groups_for(slug)
    if not groups:
        raise ValueError(f"{slug}: audio manifest lists no chapters or sources")
    staging.write_json(
        path,
        {"groups": [{"url": g.url, "chapters": list(g.chapters), "slot": g.slot} for g in groups]},
    )
    return groups


def _chapters(slug: str) -> tuple[list[int], dict[int, str]]:
    """Chapters + their ``chapter_sources`` url, from the (post-split) manifest."""
    by_chapter = sources.chapter_sources(slug)
    return sorted(by_chapter), by_chapter


def _acquire(run: dict, groups: list[SourceGroup]) -> None:
    slug, run_id = run["slug"], run["run_id"]
    job_id = run.get("acquire_job_id")
    if not job_id:
        from services.db import repo_catalog

        delivery = repo_catalog.find_delivery(slug)
        channels = delivery.channels if delivery else None
        job_id = stage_acquire.launch(slug, run_id, groups=groups, channels=channels)
        _mark(run_id, acquire_job_id=job_id)
    report = stage_acquire.wait(slug, run_id, job_id)
    manifest.record_acquired(slug, report.get("chapters") or {})
    progress.set_detail(run_id, chapters_done=sum(g.weight for g in groups))


def _mark(run_id: str, **fields) -> None:
    with durable_transaction():
        repo_align_runs.update(run_id, **fields)
    cache.invalidate_admin_requests_cache()


def _reconcile() -> None:
    """Let auto_detect fire ``alignment_completed`` now rather than on its next tick."""
    try:
        from services.segments import auto_detect

        auto_detect.reconcile_once()
    except Exception as exc:  # noqa: BLE001 — the periodic loop is the backstop
        log.warning("align: post-assemble reconcile failed: %s", exc)


def _reset_for_tests() -> None:
    with _lock:
        _workers.clear()
