"""Unified job aggregation for the admin **Jobs** tab.

Reads the one job-record store (``records.list_all``) and merges it with the
live HF job list (``base.list_in_flight_jobs``), deduping by ``job_id`` and
preferring the live ``running`` status. This is the single source the Jobs tab
renders — no DB stitching, because every kind now records uniformly at launch +
completion (``services/admin/jobs/records.py``).
"""

from __future__ import annotations

import logging

from qua_shared.schemas import JobRecord

from . import base, records

log = logging.getLogger("inspector")

#: Every kind the live HF list is scanned for (label ``task`` values).
ALL_KINDS = (
    "timestamps",
    "hf_publish",
    "hf_publish_batch",
    "cut_release",
    "refresh_catalog",
    "acquire_audio",
    "split_audio",
)


def list_all_jobs() -> list[JobRecord]:
    """Unified job list (store ∪ live), deduped by job_id, newest first.

    TTL-cached (``cache.get_all_jobs_cache``) — invalidated at every launch /
    complete / cancel so a freshly recorded job appears on the next poll.
    """
    from services.storage import cache as _cache

    cached = _cache.get_all_jobs_cache()
    if cached is not None:
        return [JobRecord.model_validate(d) for d in cached]

    by_id: dict[str, JobRecord] = {}
    for rec in records.list_all():
        if rec.job_id:
            by_id[rec.job_id] = rec

    # Merge live HF jobs — a running job not yet recorded (or recorded before
    # this store existed) still surfaces; an existing record flips to running.
    try:
        live = base.list_in_flight_jobs(ALL_KINDS)
    except Exception as exc:
        log.warning("list_all_jobs: live fetch failed: %s", exc)
        live = []
    for j in live:
        jid = j.get("job_id") or ""
        if not jid:
            continue
        kind = j.get("kind") or "timestamps"
        slug = j.get("slug")
        existing = by_id.get(jid)
        if existing is not None:
            existing.status = "running"
            if not existing.url and j.get("url"):
                existing.url = j.get("url")
            if not existing.started_at and j.get("started_at"):
                existing.started_at = j.get("started_at")
        else:
            by_id[jid] = JobRecord(
                job_id=jid,
                kind=kind,  # type: ignore[arg-type]
                slug=slug,
                status="running",
                started_at=j.get("started_at"),
                url=j.get("url"),
            )

    out = sorted(by_id.values(), key=lambda r: r.started_at or "", reverse=True)
    _cache.set_all_jobs_cache([r.model_dump(mode="json") for r in out])
    return out


def job_detail(kind: str, job_id: str, slug: str | None) -> JobRecord | None:
    """Full record for the detail drawer. Enriches a running ``timestamps`` job
    with its live log tail (the store record carries logs only post-completion)."""
    rec = records.read(kind, slug, job_id)
    if rec is None:
        return None
    if rec.status == "running" and kind == "timestamps" and slug:
        try:
            from services.admin import timestamps_jobs

            live = timestamps_jobs.job_status(slug, job_id)
            rec.logs = live.get("logs") or rec.logs
            rec.log_truncated = bool(live.get("log_truncated"))
            rec.url = live.get("url") or rec.url
            rec.status = records.normalize_status(live.get("status"))
        except Exception as exc:  # noqa: BLE001 — detail is best-effort
            log.warning("job_detail live enrich %s/%s failed: %s", slug, job_id, exc)
    return rec
