"""The single job-record store — one writer/reader for every kind.

Every job kind persists a uniform ``JobRecord`` to the bucket here: the server
writes ``running`` at launch (``record_launch``) and the terminal outcome at
completion / failure (``record_terminal``, read-merge-write so launch fields
survive). Paths reuse ``base.job_record_path`` — ``reciters/<slug>/jobs/<kind>/``
for per-slug kinds, ``jobs/_global/<kind>/`` for global kinds.

This is what makes the admin **Jobs** tab read from one place instead of
stitching bucket + DB + live. The DB stays the source-of-truth for *releases*;
these records are the uniform observability trail. The ``timestamps`` HF-Job
container ALSO self-writes its record (full logs) at the same path — unchanged,
and shape-compatible (``JobRecord`` is a superset of the legacy ``TsJobRecord``).
"""

from __future__ import annotations

import json
import logging

from qua_shared.schemas import JobRecord, TsJobSettings

from . import base

log = logging.getLogger("inspector")

#: Per-slug kinds live under ``reciters/<slug>/jobs/<kind>/``; global kinds under
#: ``jobs/_global/<kind>/``. Used to enumerate the store in ``list_all``.
PER_SLUG_KINDS = ("timestamps", "hf_publish", "acquire_audio", "split_audio")
GLOBAL_KINDS = ("hf_publish_batch", "cut_release", "refresh_catalog")

_SUCCESS = ("succeeded", "completed")
_FAILURE = (
    "failed",
    "error",
    "errored",
    "timed-out",
    "timeout",
    "stopped",
    "canceled",
    "cancelled",
    "deleted",
)


def normalize_status(raw: str | None) -> str:
    """Map an HF stage string to the small ``running``/``succeeded``/``failed``
    vocabulary the FE buckets on. Unknown / in-flight stages read as running."""
    s = (raw or "").strip().lower()
    if s in _SUCCESS:
        return "succeeded"
    if s in _FAILURE:
        return "failed"
    return "running"


def _write(rec: JobRecord) -> None:
    try:
        payload = json.dumps(rec.model_dump(exclude_none=True)).encode("utf-8")
        base.write_record_bytes(rec.kind, rec.slug, rec.job_id, payload)
    except Exception as exc:  # never block a launch/complete on a record write
        log.warning("job record write %s/%s/%s failed: %s", rec.kind, rec.slug, rec.job_id, exc)
    else:
        from services.storage import cache as _cache

        _cache.invalidate_all_jobs_cache()


def put(rec: JobRecord) -> None:
    """Write a fully-formed record verbatim (used by the one-shot backfill that
    synthesizes historical records from DB rows with their original timestamps)."""
    _write(rec)


def read(kind: str, slug: str | None, job_id: str) -> JobRecord | None:
    """Read one record as a ``JobRecord``. Injects ``kind``/``slug``/``job_id``
    from the path so older records (legacy ``TsJobRecord`` with ``type='ts'``
    and no ``kind``) still parse uniformly. None if absent/unreadable."""
    raw = base.read_record_bytes(kind, slug, job_id)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["kind"] = kind
    data["job_id"] = data.get("job_id") or job_id
    if slug is not None:
        data.setdefault("slug", slug)
    # Normalize status to the FE vocabulary. The batch record the HF Job
    # self-writes carries a ``completed_at`` but NO top-level ``status`` — infer
    # ``succeeded`` from the completion timestamp so a finished job never reads
    # as ``running`` (per-member outcomes live in ``members``).
    if data.get("status"):
        data["status"] = normalize_status(data.get("status"))
    elif data.get("ended_at") or data.get("completed_at"):
        data["status"] = "succeeded"
    try:
        return JobRecord.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — tolerate forward/legacy shapes
        log.warning("job record parse %s/%s/%s failed: %s", kind, slug, job_id, exc)
        return None


def record_launch(
    kind: str,
    slug: str | None,
    job_id: str,
    *,
    url: str | None = None,
    launched_by: str | None = None,
    settings: TsJobSettings | None = None,
) -> None:
    """Write the initial ``running`` record at launch."""
    if not job_id:
        return
    _write(
        JobRecord(
            job_id=job_id,
            kind=kind,  # type: ignore[arg-type]
            slug=slug,
            status="running",
            started_at=base.now_iso(),
            url=url,
            launched_by=launched_by,
            settings=settings,
        )
    )


def record_terminal(
    kind: str,
    slug: str | None,
    job_id: str,
    *,
    status: str,
    version: str | None = None,
    external_uri: str | None = None,
    members: list[dict] | None = None,
    validation_summary: dict | None = None,
    error: str | None = None,
    logs: list[str] | None = None,
    log_truncated: bool | None = None,
    launched_by: str | None = None,
) -> None:
    """Stamp the terminal outcome, preserving launch fields (read-merge-write).

    A record may not exist yet (e.g. the poll fallback fires for a job launched
    before this store existed, or a backfill) — in that case a fresh record is
    written from what's provided.
    """
    if not job_id:
        return
    existing = read(kind, slug, job_id)
    base_data = existing.model_dump(exclude_none=True) if existing else {}
    base_data.update(
        {
            "job_id": job_id,
            "kind": kind,
            "slug": slug if slug is not None else base_data.get("slug"),
            "status": normalize_status(status),
            "ended_at": base.now_iso(),
        }
    )
    if version is not None:
        base_data["version"] = version
    if external_uri is not None:
        base_data["external_uri"] = external_uri
    if validation_summary is not None:
        base_data["validation_summary"] = validation_summary
    if members is not None:
        base_data["members"] = members
        base_data["member_count"] = len(members)
    if error is not None:
        base_data["error"] = error
    if logs is not None:
        base_data["logs"] = logs
    if log_truncated is not None:
        base_data["log_truncated"] = log_truncated
    if launched_by is not None:
        base_data.setdefault("launched_by", launched_by)
    if not base_data.get("url"):
        base_data["url"] = base.hf_job_url(job_id)
    try:
        _write(JobRecord.model_validate(base_data))
    except Exception as exc:  # noqa: BLE001
        log.warning("record_terminal %s/%s/%s failed: %s", kind, slug, job_id, exc)


def list_for_slug(slug: str) -> list[JobRecord]:
    """Every record for one reciter across per-slug kinds."""
    out: list[JobRecord] = []
    backend = base.get_backend()
    for kind in PER_SLUG_KINDS:
        try:
            names = backend.list_dir(f"reciters/{slug}/jobs/{base.kind_dir(kind)}")
        except Exception:
            continue
        for name in names:
            if not name.endswith(".json"):
                continue
            rec = read(kind, slug, name[: -len(".json")])
            if rec is not None:
                out.append(rec)
    return out


def list_all() -> list[JobRecord]:
    """Every persisted record across every kind + every reciter.

    Walks ``jobs/_global/<kind>/`` for global kinds and
    ``reciters/<slug>/jobs/<kind>/`` for per-slug kinds (slugs from the catalog).
    Best-effort: an unreadable dir/record is skipped, never fatal.
    """
    from services.state import state as state_service

    out: list[JobRecord] = []
    backend = base.get_backend()
    for kind in GLOBAL_KINDS:
        try:
            names = backend.list_dir(f"jobs/_global/{base.kind_dir(kind)}")
        except Exception:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            rec = read(kind, None, name[: -len(".json")])
            if rec is not None:
                out.append(rec)
    try:
        slugs = [r.slug for r in state_service.all_rows()]
    except Exception as exc:
        log.warning("list_all: all_rows() failed: %s", exc)
        slugs = []
    for slug in slugs:
        out.extend(list_for_slug(slug))
    return out
