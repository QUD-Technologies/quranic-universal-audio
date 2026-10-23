"""Owner-only destructive recitation discard orchestration.

The database visibility/lifecycle change is committed before external cleanup
so a failed bucket or HF operation cannot leave content public. Cleanup is
idempotent and tracked in SQLite for retry. This module is Flask-free; routes
translate its domain errors to HTTP responses.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from qua_shared.config_loader import repo_config, template_path
from qua_shared.hf_dataset_catalog import (
    hub_published_splits_by_config,
    push_catalog_dataset,
    render_dataset_card,
    upload_dataset_card,
)
from qua_shared.schemas import Actor, ReciterRow, ReciterState, Visibility
from services import state as state_service
from services.db import connection as db_connection
from services.db import repo_discard_cleanup, repo_releases, repo_requests
from services.db import sync as db_sync
from services.state import catalog as catalog_service
from services.storage import cache, storage_paths
from services.storage.hf_bucket import delete_tree_verified, get_backend


class DiscardBusy(RuntimeError):
    def __init__(self, kind: str, job_id: str):
        super().__init__(f"{kind} job is in flight ({job_id})")
        self.kind = kind
        self.job_id = job_id


class DiscardConflict(RuntimeError):
    pass


class CleanupFailed(RuntimeError):
    pass


class CleanupPending(RuntimeError):
    pass


_PURGE_STATES = {
    ReciterState.AWAITING_REVIEW,
    ReciterState.UNDER_REVIEW,
    ReciterState.RELEASED,
}
_GLOBAL_JOB_KINDS = ("cut_release", "hf_publish_batch", "refresh_catalog")
_purge_lock = threading.Lock()


def _check_job_locks(slug: str) -> None:
    from services.admin.jobs import base as jobs_base

    busy = jobs_base.running_job_for(slug=slug)
    if busy is not None:
        raise DiscardBusy(*busy)
    for kind in _GLOBAL_JOB_KINDS:
        busy = jobs_base.running_job_for(kind=kind)
        if busy is not None:
            raise DiscardBusy(*busy)


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("INSPECTOR_HF_TOKEN") or None


def _hf_repo_id() -> str:
    repo_id = str(repo_config().get("hf_dataset") or "").strip()
    if not repo_id:
        raise CleanupFailed("HF dataset repository is not configured")
    return repo_id


def _require_hf_catalog_runtime() -> None:
    """Fail before state mutation if the synchronous catalog writer is absent."""
    try:
        from datasets import Dataset, Features, Value  # noqa: F401
    except ImportError as exc:
        raise CleanupFailed(
            "HF catalog cleanup runtime is unavailable: install the datasets package"
        ) from exc


def _matching_hf_files(*, repo_id: str, riwayah: str, slug: str, token: str | None) -> list[str]:
    from huggingface_hub import HfApi

    prefix = f"{riwayah}/{slug}-"
    paths: list[str] = []
    for item in HfApi(token=token).list_repo_tree(
        repo_id=repo_id,
        repo_type="dataset",
        recursive=True,
    ):
        path = getattr(item, "rfilename", None) or getattr(item, "path", "")
        if path.startswith(prefix) and path.endswith(".parquet") and path.count("/") == 1:
            paths.append(path)
    return sorted(set(paths))


def _preflight_hf(slug: str, riwayah: str) -> tuple[str | None, str | None, list[str]]:
    """Return ``(repo_id, token, matching_files)`` for the cleanup.

    The subset may exist even when its current release projection is stale or
    missing, so credentials are required before the database visibility change
    for every destructive discard. This keeps an orphaned subset from being
    silently left behind.
    """
    _require_hf_catalog_runtime()
    repo_id = _hf_repo_id()
    token = _hf_token()
    if token is None:
        raise CleanupFailed("HF_TOKEN is required before destructive discard")
    try:
        files = _matching_hf_files(repo_id=repo_id, riwayah=riwayah, slug=slug, token=token)
    except Exception as exc:  # noqa: BLE001 - route returns a retryable cleanup error
        raise CleanupFailed(f"could not inspect the HF dataset: {exc}") from exc
    return repo_id, token, files


def _begin_db_discard(slug: str, actor: Actor, reason: str) -> tuple[ReciterRow, bool]:
    """Hide/reset the delivery and enqueue cleanup in one durable transaction.

    Returns ``(row, newly_started)``. A failed/pending purge is resumed without
    writing a second lifecycle transition; a completed purge is idempotent.
    """
    with db_sync.durable_transaction() as conn:
        row = state_service.get_row(slug)
        if row is None:
            raise state_service.UnknownReciter(slug)
        existing = repo_discard_cleanup.get(slug)
        if row.visibility == Visibility.DISCARDED:
            if existing is None:
                raise DiscardConflict("delivery is already soft-discarded")
            if existing["status"] == "completed":
                return row, False
            if existing["status"] not in ("pending", "failed"):
                raise DiscardConflict(f"delivery cleanup is {existing['status']}")
            repo_discard_cleanup.begin(slug=slug, requested_by=actor.hf_user_id, reason=reason)
            return row, True

        def after_persist(tid: str) -> None:
            # Normally there is no pending request in these states, but close
            # a stale one defensively and retain the archive/audit linkage.
            repo_requests.resolve(
                slug=slug,
                status="discarded",
                transitioned_by=actor,
                reason=reason,
                closed_by_transition_id=tid,
            )
            at = datetime.now(UTC)
            repo_releases.supersede_current("ts", slug, except_id=-1, at=at)
            repo_releases.supersede_current("hf", slug, except_id=-1, at=at)

        new_row = state_service._apply_event(
            conn,
            slug,
            "reciter.content_discarded",
            actor=actor,
            payload={},
            reason=reason,
            after_persist=after_persist,
        )
        repo_discard_cleanup.begin(slug=slug, requested_by=actor.hf_user_id, reason=reason)
        return new_row, True


def _invalidate(slug: str) -> None:
    cache.invalidate_seg_caches(slug)
    cache.pop_audio_manifest_cache(slug)
    cache.pop_audio_url_cache(slug)
    cache.pop_reciter_peaks_response_cache(slug)
    cache.invalidate_catalog_snapshot_cache()
    cache.invalidate_public_reciters_cache()
    from services.reference import timestamps

    timestamps.invalidate()


def _purge_bucket(slug: str) -> None:
    backend = get_backend()
    delete_tree_verified(backend, storage_paths.reciter_dir(slug))


def _purge_hf(*, slug: str, riwayah: str, repo_id: str | None, token: str | None) -> int:
    if repo_id is None or token is None:
        return 0

    from huggingface_hub import CommitOperationDelete, HfApi

    files = _matching_hf_files(repo_id=repo_id, riwayah=riwayah, slug=slug, token=token)
    if files:
        HfApi(token=token).create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=[CommitOperationDelete(path_in_repo=path) for path in files],
            commit_message=f"discard recitation {slug}",
        )

    splits_by_config = hub_published_splits_by_config(repo_id=repo_id, token=token)
    remaining = {split for splits in splits_by_config.values() for split in splits}
    if slug in remaining:
        raise CleanupFailed(f"HF subset still present after deletion: {slug}")

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = push_catalog_dataset(
        repo_id=repo_id,
        db_path=Path(db_connection.db_path()),
        token=token,
        published_slugs=remaining,
        now_iso=now_iso,
    )
    card = render_dataset_card(
        template_path=template_path("hf_dataset_card"),
        splits_by_config=splits_by_config,
        stats=stats,
    )
    upload_dataset_card(repo_id=repo_id, content=card, token=token)
    return len(files)


def _mark_failed(slug: str, error: str) -> None:
    try:
        with db_sync.durable_transaction():
            repo_discard_cleanup.mark_failed(slug, error)
    except Exception as exc:  # noqa: BLE001 - retain the original failure
        raise CleanupFailed(f"{error}; could not persist cleanup failure: {exc}") from exc


def discard(slug: str, *, actor: Actor, reason: str) -> dict:
    """Perform the complete owner-triggered discard, including external purge."""
    with _purge_lock:
        _check_job_locks(slug)
        delivery = state_service.get_row(slug)
        if delivery is None:
            raise state_service.UnknownReciter(slug)
        existing_cleanup = repo_discard_cleanup.get(slug)
        if delivery.visibility == Visibility.DISCARDED:
            if existing_cleanup is None:
                raise DiscardConflict("delivery is already soft-discarded")
            if existing_cleanup["status"] == "completed":
                return {
                    "ok": True,
                    "slug": slug,
                    "state": delivery.state.value,
                    "visibility": delivery.visibility.value,
                    "cleanup_status": "completed",
                }
        catalog_delivery = catalog_service.find_delivery(slug)
        if catalog_delivery is None:
            raise state_service.UnknownReciter(slug)
        repo_id, token, _ = _preflight_hf(slug, catalog_delivery.riwayah)
        row, started = _begin_db_discard(slug, actor, reason)
        _invalidate(slug)
        if not started:
            cleanup = repo_discard_cleanup.get(slug) or {}
            return {
                "ok": True,
                "slug": slug,
                "state": row.state.value,
                "visibility": row.visibility.value,
                "cleanup_status": cleanup.get("status", "completed"),
            }

        errors: list[str] = []
        try:
            _purge_bucket(slug)
        except Exception as exc:  # noqa: BLE001 - continue HF cleanup if possible
            errors.append(f"bucket cleanup failed: {exc}")
        try:
            _purge_hf(
                slug=slug,
                riwayah=catalog_delivery.riwayah,
                repo_id=repo_id,
                token=token,
            )
        except Exception as exc:  # noqa: BLE001 - report and retry idempotently
            errors.append(f"HF cleanup failed: {exc}")

        if errors:
            message = "; ".join(errors)
            _mark_failed(slug, message)
            raise CleanupFailed(message)

        with db_sync.durable_transaction():
            repo_discard_cleanup.mark_completed(slug)
        _invalidate(slug)
        return {
            "ok": True,
            "slug": slug,
            "state": "catalogued",
            "visibility": "discarded",
            "cleanup_status": "completed",
        }


def undiscard(slug: str, *, actor: Actor, reason: str | None) -> ReciterRow:
    """Restore visibility; destructive purges restore only requestability."""
    with db_sync.durable_transaction() as conn:
        cleanup = repo_discard_cleanup.get(slug)
        if cleanup is not None and cleanup["status"] in ("pending", "failed"):
            raise CleanupPending("content cleanup must complete before undiscard")

        def after_persist(_tid: str) -> None:
            if cleanup is not None and cleanup["status"] == "completed":
                repo_discard_cleanup.mark_restored(slug)

        row = state_service._apply_event(
            conn,
            slug,
            "reciter.undiscarded",
            actor=actor,
            payload={},
            reason=reason,
            after_persist=after_persist,
        )
    _invalidate(slug)
    return row
