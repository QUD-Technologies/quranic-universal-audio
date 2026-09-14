"""Fire + inspect timestamps runs on the batch timing Space.

Flask-free. The Reviews tab triggers ``launch()`` for an under-review reciter;
the whole-verse MFA producer runs on the batch timing Space (ADR 0002 slice B),
which reads detailed.json + audio and writes v13 per-chapter shards +
``ts_validation.json`` into the inspector bucket. The Space also writes a
durable run-log record at ``reciters/<slug>/jobs/ts/<run_id>.json`` (settings +
status + logs) — ``running`` at accept, then ``succeeded``/``failed`` when the
run ends. Status is that record (``job_status`` reads it); the run id is
appended to the reciter's ``timestamps_job_ids``.

Launching does NOT transition the reciter — it stays UNDER_REVIEW (marked_ready)
while the run proceeds, so a failed run is recoverable (just re-run). On
*success* ``complete_timestamps_job`` takes one of two paths by current state:

  - **First publish** (under_review marked_ready) → fires ``reciter.published``
    (→ released) with ``SYSTEM_ACTOR``.
  - **Regenerate** (already released) → NO transition (there is no
    released → released edge). It records the new ``ts`` release, supersedes the
    prior one, stamps the slug's HF/GH releases stale, and writes a
    ``reciter.ts_regenerated`` audit event so the operator is driven to
    re-publish from the Releases tab.

``complete_timestamps_job`` runs as the idempotent ``job_status`` poll fallback
when the drawer sees a terminal-success record. It is idempotent on the
``(track='ts', slug, version=run_id)`` triple, so a repeated poll dispatch is a
no-op after the first.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from datetime import UTC

from qua_shared.schemas import StaleReason, TsJobRecord, TsJobSettings
from services.state import state as state_service
from services.storage.hf_bucket import StorageNotFound, get_backend

log = logging.getLogger("inspector")

# Terminal HF stages (anything not in this set is treated as in-flight).
_TERMINAL = (
    "succeeded",
    "completed",
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
# Terminal stages that mean the alignment finished cleanly. HF has reported
# both "succeeded" and "completed" for clean exits across job types, so the
# auto-release path treats either as success (the job's own self-POST always
# sends the literal "succeeded").
_TERMINAL_SUCCESS = ("succeeded", "completed")

#: How long a ``running`` record may sit before it is treated as dead.
#: The Space owns the record and stamps it terminal when a run ends, so a run
#: whose Space restarted (or was rebuilt) mid-flight leaves ``running`` behind
#: forever — and the single-flight guard then refuses every relaunch with "a
#: timestamps job is already running". A full 114-chapter run lands in ~1-2 h,
#: so anything past this ceiling is a corpse, not a run. Env-overridable.
_STALE_RUN_HOURS_DEFAULT = 2.0


def _stale_run_hours() -> float:
    raw = (os.environ.get("INSPECTOR_TS_STALE_RUN_HOURS") or "").strip()
    try:
        return float(raw) if raw else _STALE_RUN_HOURS_DEFAULT
    except ValueError:
        return _STALE_RUN_HOURS_DEFAULT


def _is_stale_running(rec: dict | None) -> bool:
    """True when ``rec`` claims ``running`` but started long enough ago that the
    Space cannot still be on it. Unparseable ``started_at`` is never stale."""
    if not rec or rec.get("status") != "running":
        return False
    started = _parse_iso(rec.get("started_at"))
    if started is None:
        return False
    age_h = (datetime.datetime.now(UTC) - started).total_seconds() / 3600.0
    return age_h > _stale_run_hours()


def _job_record_path(slug: str, job_id: str) -> str:
    """Bucket path for a job's durable record. Per-reciter under
    ``reciters/<slug>/jobs/ts/`` — colocated with all the reciter's other
    content (detailed/audio/peaks/timestamps/edit_history), consistent with the
    bucket convention that everything for a reciter lives under its folder."""
    return f"reciters/{slug}/jobs/ts/{job_id}.json"


def _legacy_job_record_path(job_id: str) -> str:
    """Pre-2026-05 top-level path. Read-only fallback so records written before
    the per-reciter move still surface in the panel."""
    return f"jobs/ts/{job_id}.json"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# In-flight / history signals — read from the bucket run-log the Space writes.
# The producer moved to the batch timing Space (ADR 0002 slice B); there is no
# HF Job to inspect, so "what is running / what failed / when did it start"
# comes from each reciter's newest ``jobs/ts/<run_id>.json`` record.
# ---------------------------------------------------------------------------


def _parse_iso(raw: str | None) -> datetime.datetime | None:
    """Aware-UTC datetime from a run-log ISO timestamp, or None."""
    if not raw:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _newest_ts_record(slug: str) -> dict | None:
    """The reciter's most recent ts run-log record (``timestamps_job_ids`` is
    append-order, newest last), or None when it has never run one."""
    row = state_service.get_row(slug)
    ids = list(getattr(row, "timestamps_job_ids", []) or []) if row else []
    return read_job_record(slug, ids[-1]) if ids else None


def running_job_for(slug: str) -> str | None:
    """Run id of an in-flight timestamps run for ``slug`` (single-flight guard),
    else None. The Space serves one full-reciter run at a time and rejects a
    second POST, so this only needs the newest record's status.

    A ``running`` record older than the staleness ceiling is reported as NOT
    running: the Space never stamped it terminal (restart / rebuild mid-run),
    and without this the slug can never be relaunched from the UI."""
    rec = _newest_ts_record(slug)
    if not rec or rec.get("status") != "running":
        return None
    if _is_stale_running(rec):
        log.warning(
            "ts run %s for %s is stale (started %s, ceiling %.1fh) — treating as dead",
            rec.get("job_id"),
            slug,
            rec.get("started_at"),
            _stale_run_hours(),
        )
        return None
    return rec.get("job_id")


def latest_terminal_failed_slugs() -> set[str]:
    """Slugs whose MOST-RECENT timestamps run ended in a non-success terminal
    status. The auto-gen guard reads this so it never re-launches a slug that
    just failed — it stays in "ready to generate" for a manual retry."""
    out: set[str] = set()
    for row in state_service.all_rows():
        rec = _newest_ts_record(getattr(row, "slug", ""))
        status = (rec or {}).get("status")
        if status in _TERMINAL and status not in _TERMINAL_SUCCESS:
            out.add(row.slug)
    return out


def latest_job_started_by_slug() -> dict[str, datetime.datetime]:
    """Map ``slug`` to the start time of its MOST-RECENT timestamps run (any
    status). The automations use it as a relaunch watermark so a regen already
    fired for a staleness/readiness mark is not re-fired every tick while
    completion settles."""
    out: dict[str, datetime.datetime] = {}
    for row in state_service.all_rows():
        rec = _newest_ts_record(getattr(row, "slug", ""))
        dt = _parse_iso((rec or {}).get("started_at"))
        if dt is not None:
            out[row.slug] = dt
    return out


def terminal_success_runs() -> list[tuple[str, str]]:
    """Return newest linked Space runs that completed successfully.

    Timestamp runs live in a persistent Space and never appear in
    ``huggingface_hub.list_jobs()``. The shared release poller consumes this
    durable bucket signal so completion does not depend on an open job drawer.
    """

    out: list[tuple[str, str]] = []
    for row in state_service.all_rows():
        slug = getattr(row, "slug", "")
        ids = list(getattr(row, "timestamps_job_ids", []) or [])
        if not slug or not ids:
            continue
        job_id = ids[-1]
        rec = read_job_record(slug, job_id)
        if rec and str(rec.get("status") or "").lower() in _TERMINAL_SUCCESS:
            out.append((slug, job_id))
    return out


def in_flight_runs() -> list[dict]:
    """Every reciter with a currently-``running`` ts run, as in-flight records
    (``{kind, slug, job_id, started_at, url}``) for ``jobs_base``'s shared
    in-flight registry. At most one — the Space serializes full-reciter runs."""
    out: list[dict] = []
    for row in state_service.all_rows():
        slug = getattr(row, "slug", "")
        rec = _newest_ts_record(slug)
        if rec and rec.get("status") == "running" and not _is_stale_running(rec):
            out.append(
                {
                    "kind": "timestamps",
                    "slug": slug,
                    "job_id": rec.get("job_id", ""),
                    "started_at": rec.get("started_at"),
                    "url": rec.get("url"),
                }
            )
    return out


def launch(slug: str, *, settings: TsJobSettings, webhook_base: str | None = None) -> dict:
    """Fire the whole-verse timestamps run for ``slug`` on the batch timing
    Space and link its run id to the reciter.

    ``settings`` carries the admin's form choices; the Space owns the model,
    method and padding now, so only ``beams`` + ``chapters`` (affected-only
    regen scope) reach it. The Space writes the ``running`` run-log record
    synchronously before it returns the run id, so the panel can show the run
    immediately; there is no HF Job to stage code for. ``webhook_base`` is
    unused (completion is the polled run-log, not a callback) and kept only for
    call-site compatibility. Returns ``{"job_id", "url"}``.

    Does NOT transition the reciter — it stays UNDER_REVIEW (marked_ready) while
    the run proceeds. On success ``complete_timestamps_job`` publishes it via the
    ``job_status`` poll fallback. Caller must enforce single-flight via
    ``running_job_for`` first.
    """
    from services.admin import ts_space_client
    from services.reference.delivery_edition import sdk_riwayah_for

    if state_service.get_row(slug) is None:
        raise ValueError(f"unknown slug {slug}")

    # The Space aligns against Hafs and projects, so it has to be told which
    # edition the delivery is in — a Hafs-proxy run stamped as Hafs would ship
    # the wrong coordinates into the shard.
    run_id = ts_space_client.start_run(
        slug,
        chapters=settings.chapters,
        beams=settings.beams,
        riwayah=sdk_riwayah_for(slug),
    )
    state_service.record_timestamps_job(slug, run_id)
    # Bust the in-flight cache so the next /releases/status fetch shows the
    # running job immediately (the Releases tab watches the ``timestamps`` kind).
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    log.info("launched timestamps run %s for %s on the batch Space", run_id, slug)
    return {"job_id": run_id, "url": None}


def _write_job_record(rec: TsJobRecord) -> None:
    """Write the durable job record to the bucket (best-effort)."""
    try:
        get_backend().write_json_atomic(
            _job_record_path(rec.slug, rec.job_id), rec.model_dump(exclude_none=True)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("write job record %s failed: %s", rec.job_id, exc)


def _read_record_bytes(slug: str, job_id: str) -> bytes | None:
    """Read the record bytes, preferring the per-reciter path and falling back
    to the legacy top-level ``jobs/ts/`` location for pre-move records."""
    backend = get_backend()
    for path in (_job_record_path(slug, job_id), _legacy_job_record_path(job_id)):
        try:
            return backend.read_bytes(path)
        except StorageNotFound:
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("read job record %s failed: %s", job_id, exc)
            return None
    return None


def read_job_record(slug: str, job_id: str) -> dict | None:
    """Return the persisted record for one job, or None if absent/unreadable."""
    raw = _read_record_bytes(slug, job_id)
    if raw is None:
        return None
    try:
        return TsJobRecord.model_validate(json.loads(raw)).model_dump(exclude_none=True)
    except Exception:  # noqa: BLE001 — tolerate older/forward shapes
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return None


def list_job_records(slug: str) -> list[dict]:
    """Persisted records for every job linked to ``slug`` (newest first).

    Reads ``reciters/<slug>/jobs/ts/<id>.json`` for each id in the reciter's
    ``timestamps_job_ids``. Missing records (e.g. a launch that never wrote)
    surface as a minimal stub so the panel can still show the id."""
    row = state_service.get_row(slug)
    ids = list(getattr(row, "timestamps_job_ids", []) or []) if row else []
    out: list[dict] = []
    for jid in ids:
        rec = read_job_record(slug, jid)
        out.append(rec or {"job_id": jid, "slug": slug, "type": "ts", "status": "unknown"})
    out.reverse()  # timestamps_job_ids is append-order → newest last
    return out


def job_status(slug: str, job_id: str, *, log_tail: int = 400) -> dict:
    """Status + bounded log tail for a run, from the bucket run-log record the
    Space writes (``running`` at accept, terminal + logs at the end).

    On a terminal-success record this fires the idempotent
    ``complete_timestamps_job`` (publish / regen); a terminal-failure lights the
    Reviews-tab dot. Both are no-ops after the first dispatch, so the drawer poll
    settling the release is safe to repeat.
    """
    rec = read_job_record(slug, job_id)
    if rec is None:
        return {
            "job_id": job_id,
            "status": "unknown",
            "url": None,
            "logs": [],
            "log_truncated": False,
        }
    status = str(rec.get("status") or "").lower()
    logs = list(rec.get("logs") or [])
    truncated = bool(rec.get("log_truncated"))
    if len(logs) > log_tail:
        logs = logs[-log_tail:]
        truncated = True

    if status in _TERMINAL_SUCCESS:
        try:
            complete_timestamps_job(slug, job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("auto-release on poll for %s failed: %s", slug, exc)
    elif status in _TERMINAL:
        note_timestamps_job_failed(slug)

    return {
        "job_id": job_id,
        "status": status,
        "url": rec.get("url"),
        "logs": logs,
        "log_truncated": truncated,
    }


def _ts_regen_provenance(slug: str) -> tuple[str | None, list[int] | None]:
    """Provenance for a new ``ts`` row, read from the row it will supersede.

    Returns ``(prior_ts_version, affected_chapters)`` — the superseded version and
    the chapters edited since its generation (what this regen folds in). Both
    ``None`` on a first publish (no prior ts row). Call BEFORE ``supersede_current``
    (a superseded row drops out of ``current_release``). Best-effort: a failed
    edit-history read yields no affected chapters."""
    from services.db import repo_releases
    from services.segments import ts_staleness

    prior = repo_releases.current_release("ts", slug)
    if prior is None:
        return None, None
    produced_at = prior.get("produced_at")
    affected: list[int] | None = None
    if produced_at:
        info = ts_staleness.ts_stale_info(slug, produced_at=produced_at)
        if info:
            affected = info["affected_chapters"]
    return prior.get("version"), affected


def _recheck_report_staleness(slug: str, affected_chapters: list[int] | None) -> None:
    """Best-effort: flag Timestamps reports invalidated by this regeneration.

    Re-resolves each open report's target against the new shards and stales those
    whose category-relevant content changed. Runs inside the caller's
    ``durable_transaction`` (the repo write needs it); a failure here must never
    abort the release write."""
    try:
        from services.ts_reports import ts_target_snapshot

        ts_target_snapshot.recheck_reports_staleness(slug, affected_chapters)
    except Exception:  # noqa: BLE001 — best-effort; never break the release write
        log.exception("recheck report staleness failed for %s", slug)


def complete_timestamps_job(slug: str, job_id: str) -> dict:
    """Record a succeeded timestamps job for ``slug``. Idempotent.

    The single completion path, reached from both the job-completion webhook
    (prod) and the ``job_status`` poll fallback (dev / missed webhook). It reads
    the row first and branches by current state, so a double-fire (webhook +
    poll, or two concurrent webhooks) no-ops the loser:

    - ``released`` → **regenerate** (``_regenerate_timestamps_on_released``): no
      transition, just records the new ``ts`` release + stamps HF/GH stale.
      Idempotent on ``(track='ts', slug, version=job_id)``: the first publish's
      ``ts`` row (version=job_id) makes the poll re-fire of that same job a
      no-op, while a fresh regen run carries a new job_id and proceeds.
    - ``under_review`` + ``marked_ready`` + at least one shard on the bucket →
      first publish: ``reciter.published`` (→ released) via ``SYSTEM_ACTOR``
      (role OWNER, so the ``reciter.publish`` gate passes).
    - anything else (not marked_ready, no shards, other state) → log + no-op.

    The ``transition()`` call is wrapped in ``StateError`` handling to absorb
    the TOCTOU window where two callers both read ``under_review`` before
    either commits (the single-writer + ``BEGIN IMMEDIATE`` serializes them; the
    loser's handler raises ``_state_precondition`` once the row is already
    released). Returns ``{slug, state, released: bool, regenerated?: bool, reason?}``.
    """
    # Lazy import: SYSTEM_ACTOR lives in the segments package, which pulls
    # bucket loaders — keep it off this module's import-time graph.
    from services.segments.auto_detect import SYSTEM_ACTOR

    row = state_service.get_row(slug)
    if row is None:
        return {"slug": slug, "state": None, "released": False, "reason": "unknown slug"}
    if row.state.value == "released":
        return _regenerate_timestamps_on_released(slug, job_id)
    if row.state.value != "under_review" or not row.marked_ready:
        log.info(
            "complete_timestamps_job(%s): not publishable (state=%s "
            "marked_ready=%s) — leaving as-is",
            slug,
            row.state.value,
            row.marked_ready,
        )
        return {
            "slug": slug,
            "state": row.state.value,
            "released": False,
            "reason": "not marked-ready / wrong state",
        }
    if not _has_any_shard(slug):
        log.warning(
            "complete_timestamps_job(%s): job %s succeeded but no "
            "timestamps shards on the bucket — not publishing",
            slug,
            job_id,
        )
        return {"slug": slug, "state": row.state.value, "released": False, "reason": "no shards"}

    # v2: wrap transition + release-row write in one outer durable_transaction
    # so they commit atomically. ``durable_transaction()`` is nesting-safe —
    # the inner ``state.transition`` uses a SAVEPOINT and only the outermost
    # boundary uploads (avoids double-bucket-write). The TS gen completion is
    # the source-of-truth event for ``per_recitation_releases(track='ts')``:
    # it inserts the new ts row, supersedes the prior current ts row, and
    # stamps the slug's HF/GH releases as stale (TS regen invalidates them).
    from datetime import datetime

    from services.db import repo_releases
    from services.db.sync import durable_transaction

    prior_ts_version, affected_chapters = _ts_regen_provenance(slug)
    try:
        with durable_transaction() as _:
            new_row = state_service.transition(
                slug,
                "reciter.published",
                actor=SYSTEM_ACTOR,
                payload={"job_id": job_id},
            )
            now = datetime.now(UTC)
            # Supersede prior current ts row FIRST — partial-unique on (track,
            # slug) WHERE superseded_at IS NULL blocks two current rows.
            repo_releases.supersede_current("ts", slug, except_id=-1, at=now)
            repo_releases.insert_per_recitation_release(
                track="ts",
                slug=slug,
                version=job_id,
                produced_at=now,
                produced_by="SYSTEM_ACTOR",
                produced_by_job_id=job_id,
                affected_chapters=affected_chapters,
                prior_ts_version=prior_ts_version,
            )
            # Stamp the HF + most-recent-GH membership as stale (re-publishing
            # clears stale in v1; no explicit ack endpoint).
            repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
            _recheck_report_staleness(slug, affected_chapters)
    except state_service.StateError as exc:
        # Lost a double-fire race, or the row changed under us (e.g. reviewer
        # un-marked). Benign — the winning caller (or a re-run) handles it.
        log.info("complete_timestamps_job(%s): transition skipped: %s", slug, exc)
        return {
            "slug": slug,
            "state": (state_service.get_row(slug) or row).state.value,
            "released": False,
            "reason": "transition skipped",
        }
    # Light the Reviews-tab dot on the (now released) Published-bucket row.
    _note_job_finished(slug)
    # Drop the now-terminal job from the Releases-tab in-flight signal promptly.
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    log.info("complete_timestamps_job(%s): published (job=%s)", slug, job_id)
    return {"slug": slug, "state": new_row.state.value, "released": True}


def _regenerate_timestamps_on_released(slug: str, job_id: str) -> dict:
    """Record a TS regen for an already-``released`` reciter — no transition.

    The first publish fires ``reciter.published`` (under_review → released). A
    second-and-onwards TS run on the same reciter must NOT re-fire that edge
    (there is no released → released transition). Instead it records the new
    ``ts`` release, supersedes the prior current one, stamps the slug's HF + most
    recent-GH releases stale (so the operator is driven to re-publish), and
    writes a ``reciter.ts_regenerated`` audit row — visibly distinct from the
    first publish.

    Idempotent: the poll fallback re-dispatches every terminal-success job, so we
    skip when ``(track='ts', slug, version=job_id)`` is already recorded. The
    first publish's ``ts`` row (version=job_id) therefore also makes a later poll
    re-fire of that same job a no-op here. Returns
    ``{slug, state: 'released', released: False, regenerated: bool, reason?}``.
    """
    from datetime import datetime

    from services.db import repo_releases
    from services.db.sync import durable_transaction
    from services.segments.auto_detect import SYSTEM_ACTOR
    from services.state import audit

    if repo_releases.release_by_version("ts", slug, job_id) is not None:
        log.info("complete_timestamps_job(%s): ts %s already recorded — no-op", slug, job_id)
        return {
            "slug": slug,
            "state": "released",
            "released": False,
            "reason": "ts already recorded",
        }
    if not _has_any_shard(slug):
        log.warning(
            "complete_timestamps_job(%s): regen job %s succeeded but no "
            "timestamps shards on the bucket — not recording",
            slug,
            job_id,
        )
        return {"slug": slug, "state": "released", "released": False, "reason": "no shards"}

    prior_ts_version, affected_chapters = _ts_regen_provenance(slug)
    now = datetime.now(UTC)
    with durable_transaction() as _:
        # Supersede prior current ts row FIRST — partial-unique on (track, slug)
        # WHERE superseded_at IS NULL blocks two current rows.
        repo_releases.supersede_current("ts", slug, except_id=-1, at=now)
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version=job_id,
            produced_at=now,
            produced_by="SYSTEM_ACTOR",
            produced_by_job_id=job_id,
            affected_chapters=affected_chapters,
            prior_ts_version=prior_ts_version,
        )
        # Re-publishing clears stale; TS regen sets it on the HF/GH membership.
        repo_releases.stamp_stale(slug, at=now, reason=StaleReason.TS_REGEN)
        _recheck_report_staleness(slug, affected_chapters)
        audit.append(
            "reciter.ts_regenerated",
            actor=SYSTEM_ACTOR,
            slug=slug,
            from_state="released",
            to_state="released",
            payload={"job_id": job_id},
        )
    _note_job_finished(slug)
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    log.info("complete_timestamps_job(%s): ts regenerated (job=%s)", slug, job_id)

    # Email subscribers who follow this reciter's timestamps (best-effort).
    try:
        from services.email import emit as _email
        from services.state import catalog

        delivery = catalog.find_delivery(slug)
        if delivery is not None:
            _email.emit_timestamps_regenerated(
                reciter_id=delivery.reciter_id, reciter_name=catalog.display_name(slug) or slug
            )
    except Exception:  # noqa: BLE001
        log.exception("email ts_regenerated hook failed (slug=%s)", slug)

    return {"slug": slug, "state": "released", "released": False, "regenerated": True}


def cancel_job(slug: str, job_id: str) -> dict:
    """Mark a timestamps run canceled in its durable record.

    The run executes on the batch timing Space, which exposes no cancel route,
    so this does not hard-kill it — the Space's serialized run finishes and its
    output-presence resume makes a stale run harmless (a later run skips shards
    already written). Overwriting the ``running`` record with ``status=canceled``
    keeps the side-panel history and single-flight guard honest. No lifecycle
    transition: the reciter stays in its current state, so a fresh launch picks
    it up. Idempotent — only rewrites a non-terminal record.
    """
    if state_service.get_row(slug) is None:
        return {"slug": slug, "job_id": job_id, "canceled": False, "reason": "unknown slug"}

    existing = read_job_record(slug, job_id)
    if existing is None or existing.get("status") in (None, "running", "unknown"):
        base = dict(existing) if existing else {"job_id": job_id, "slug": slug, "type": "ts"}
        base["status"] = "canceled"
        base.setdefault("ended_at", _now_iso())
        try:
            _write_job_record(TsJobRecord.model_validate(base))
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel record write %s failed: %s", job_id, exc)

    # Light the Reviews-tab dot — same notify path as failure.
    _note_job_finished(slug)
    log.info("cancel_job(%s, %s): marked canceled (Space run not hard-killed)", slug, job_id)
    return {"slug": slug, "job_id": job_id, "canceled": True}


def note_timestamps_job_failed(slug: str) -> dict:
    """Record a FAILED timestamps job so the Reviews-tab dot lights up.

    No lifecycle change — the reciter stays under_review (marked_ready) and is
    re-runnable. Just stamps ``last_job_finished_at`` (best-effort) so the admin
    is notified on the Marked-ready bucket. Idempotent-ish: each terminal
    failure re-stamps, re-lighting the dot until the admin views the row."""
    row = state_service.get_row(slug)
    if row is None:
        return {"slug": slug, "noted": False, "reason": "unknown slug"}
    _note_job_finished(slug)
    return {"slug": slug, "noted": True, "state": row.state.value}


def _note_job_finished(slug: str) -> None:
    """Best-effort ``last_job_finished_at`` stamp — never raise into callers."""
    try:
        state_service.mark_timestamps_job_finished(slug)
    except Exception as exc:  # noqa: BLE001
        log.warning("mark_timestamps_job_finished(%s) failed: %s", slug, exc)


def _has_any_shard(slug: str) -> bool:
    """True if at least one per-chapter timestamps shard exists for ``slug``.

    Guards auto-release against a job that exits 0 without writing shards. One
    cheap bucket listing of ``reciters/<slug>/timestamps/``."""
    try:
        names = get_backend().list_dir(f"reciters/{slug}/timestamps")
        return any(str(n).endswith(".json.br") for n in names)
    except StorageNotFound:
        return False
    except Exception as exc:  # noqa: BLE001 — never let the check break release
        log.warning("shard-existence check for %s failed: %s", slug, exc)
        return False
