"""The automation evaluators — one per automation, each idempotent.

Every evaluator: (1) returns early if disabled; (2) derives candidates from live
release state; (3) skips in-flight + failed-bucket rows (no auto-retry); (4)
launches via the existing job entrypoints; (5) records the run. They write to the
DB ONLY when they act (or a schedule advances), so an idle tick uploads nothing.

Engine contract: each takes the parsed ``AutomationConfig`` + a tz-aware UTC
``now`` and is wrapped in its own try/except by ``engine.tick`` — a raising
evaluator never kills the tick or the other automations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from config import AUTOMATION_PUBLIC_BASE_URL
from qua_shared.schemas import AutomationConfig, TsJobSettings
from services.admin import timestamps_jobs
from services.admin.jobs import base as jobs_base
from services.admin.jobs import cut_release as cut_release_jobs
from services.admin.jobs import hf_publish_batch as hf_publish_batch_jobs
from services.admin.jobs import refresh_catalog as refresh_catalog_jobs
from services.admin.release_preview import current_auto_version
from services.db import _serde, get_conn, repo_automation, repo_claims, repo_releases
from services.db.sync import durable_transaction
from services.segments import ts_staleness
from services.segments.flags import count_flagged
from services.storage.data_loader import load_detailed

from .actor import SYSTEM_AUTOMATION_ID, system_actor
from .config import load_config
from .integrity import eval_shard_integrity
from .schedule import is_due

logger = logging.getLogger(__name__)

# Automation ids — the keys in ``automation_state`` and the FE status rows.
AUTO_GEN_TS = "auto_gen_ts"
GH_CUT = "gh_cut"
HF_PUBLISH = "hf_publish"
STALE_TS_REGEN = "stale_ts_regen"
STALE_METADATA = "stale_metadata"
AUTO_RELEASE_INACTIVE = "auto_release_inactive"

#: Every release-job kind — used to exclude any slug with a job already running.
_ALL_JOB_KINDS = (
    "hf_publish",
    "hf_publish_batch",
    "cut_release",
    "timestamps",
    "refresh_catalog",
)


def _webhook_base() -> str | None:
    return AUTOMATION_PUBLIC_BASE_URL or None


def _ts_settings(
    cfg: AutomationConfig,
    *,
    chapters: list[int] | None = None,
) -> TsJobSettings:
    """Build a ``TsJobSettings`` for an automated launch from the shared
    ``ts_generation_defaults`` — the single owner-wide source the Releases-tab
    "Timestamps generation" accordion edits.

    All TS tunables (beam/model/workers/batch_size/download_workers/
    padding/method) come from the shared defaults; only ``chapters`` is the
    automation's own per-launch scope choice.
    """
    d = cfg.ts_generation_defaults
    return TsJobSettings(
        beams=[d.beam],
        aligner_model=d.aligner_model,
        chapters=chapters or None,
        workers=d.workers,
        batch_size=d.batch_size,
        download_workers=d.download_workers,
        padding=d.padding,
        method=d.method,
    )


def _record(
    automation_id: str,
    *,
    status: str,
    detail: str | None = None,
    last_run_at: datetime | None = None,
    now: datetime | None = None,
) -> None:
    with durable_transaction() as _:
        repo_automation.record_run(
            automation_id, status=status, detail=detail, last_run_at=last_run_at, now=now
        )


def _in_flight_slugs(kinds: tuple[str, ...]) -> set[str]:
    return {j["slug"] for j in jobs_base.list_in_flight_jobs(kinds) if j.get("slug")}


def _claim_val(claim, key: str):
    """Read a column from a claim that may be a sqlite3.Row or a dict; None if absent."""
    try:
        return claim[key]
    except (KeyError, IndexError, TypeError):
        return None


def _already_launched_since(
    watermarks: dict[str, datetime], slug: str, since: datetime | None
) -> bool:
    """True iff a timestamps job for ``slug`` was already launched at/after
    ``since`` — i.e. a regen covering this staleness/readiness watermark is
    already pending. Guards against re-firing while completion (webhook / 120 s
    poll) lags the 60 s tick. ``since`` None (no watermark) → never skip on this."""
    if since is None:
        return False
    started = watermarks.get(slug)
    return started is not None and started >= since


def _delivery_slugs() -> list[str]:
    return [
        r[0] for r in get_conn().execute("SELECT slug FROM deliveries ORDER BY slug").fetchall()
    ]


# ---------------------------------------------------------------------------
# 1. Auto-generate timestamps on mark-ready (event-reconciled).
# ---------------------------------------------------------------------------


def eval_auto_gen_ts(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.auto_gen_ts
    if not c.enabled:
        return
    rows = (
        get_conn()
        .execute(
            "SELECT d.slug FROM deliveries d "
            "JOIN delivery_states ds ON ds.slug = d.slug "
            "JOIN claims cl ON cl.slug = d.slug AND cl.released_at IS NULL "
            "WHERE ds.state = 'under_review' AND cl.marked_ready_at IS NOT NULL "
            "ORDER BY d.slug"
        )
        .fetchall()
    )
    if not rows:
        return
    failed = timestamps_jobs.latest_terminal_failed_slugs()
    in_flight = _in_flight_slugs(("timestamps",))
    watermarks = timestamps_jobs.latest_job_started_by_slug()
    launched: list[str] = []
    for r in rows:
        slug = r[0]
        if repo_releases.current_release("ts", slug) is not None:
            continue  # already timestamped
        if slug in failed or slug in in_flight:
            continue  # last gen failed (manual retry) or already running
        claim = repo_claims.get_open_claim(slug)
        if claim is None or _auto_gen_gated(c, slug, claim):
            continue
        # Already kicked a gen for this mark-ready? Wait for it — the ts release
        # (which stops this loop) lands only when completion settles, lagging the
        # tick. Without this, a succeeded-but-not-completed job re-fires every tick.
        if _already_launched_since(
            watermarks, slug, _serde.from_iso(_claim_val(claim, "marked_ready_at"))
        ):
            continue
        try:
            timestamps_jobs.launch(
                slug,
                settings=_ts_settings(cfg),
                webhook_base=_webhook_base(),
            )
            launched.append(slug)
        except Exception as exc:  # noqa: BLE001 — one slug's failure never blocks the rest
            logger.warning("auto_gen_ts: launch for %s failed: %s", slug, exc)
    if launched:
        _record(
            AUTO_GEN_TS,
            status="launched",
            detail="generated timestamps for " + ", ".join(launched),
            last_run_at=now,
            now=now,
        )


def _auto_gen_gated(c, slug: str, claim) -> bool:
    """True → skip auto-gen for ``slug`` (a human should look first).

    Mark-ready bypass is owner-only and means "I've vetted this, no checklist
    needed" — it does NOT gate auto-gen. Only the owner-configured comment/flag
    gates do.
    """
    if c.gate_by_comments:
        if (claim["mark_ready_comment_checks"] or "").strip():
            return True
        if (claim["mark_ready_comment_issues"] or "").strip():
            return True
    if c.gate_by_flags:
        try:
            if count_flagged(load_detailed(slug)) > 0:
                return True
        except Exception:  # noqa: BLE001 — can't verify flags → gate (fail-safe)
            logger.warning("auto_gen_ts: flag count for %s failed — gating", slug)
            return True
    return False


# ---------------------------------------------------------------------------
# 2. Scheduled GH cut (owner timezone).
# ---------------------------------------------------------------------------


def eval_gh_cut(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.gh_cut
    if not c.enabled:
        return
    st = repo_automation.get_state(GH_CUT)
    last_run = _serde.from_iso(st["last_run_at"]) if st else None
    if not is_due(
        last_run_at=last_run,
        interval_days=c.interval_days,
        time_of_day=c.time_of_day,
        timezone=c.timezone,
        now_utc=now,
    ):
        return
    # Global single-flight — retry next tick without advancing the cadence.
    if (
        jobs_base.running_job_for(kind="cut_release") is not None
        or jobs_base.running_job_for(kind="hf_publish_batch") is not None
    ):
        return
    _version, count = current_auto_version()
    if count == 0:
        # Nothing changed — advance the cadence so we don't re-check every tick.
        _record(GH_CUT, status="skipped", detail="no changes to cut", last_run_at=now, now=now)
        return
    override = c.next_version_override
    try:
        cut_release_jobs.launch(
            version=override or None,  # None → the job auto-computes the version
            launched_by=SYSTEM_AUTOMATION_ID,
            webhook_base=_webhook_base(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gh_cut: launch failed: %s", exc)
        _record(GH_CUT, status="error", detail=str(exc)[:200], now=now)  # cadence unchanged
        return
    detail = f"cut release {override}" if override else "cut release"
    # One-shot override: clear it, advance the cadence, all in one durable write.
    with durable_transaction() as _:
        if override:
            cleared = cfg.model_copy(
                update={"gh_cut": c.model_copy(update={"next_version_override": None})}
            )
            repo_automation.set_config_json(
                cleared.model_dump_json(), updated_by=SYSTEM_AUTOMATION_ID, now=now
            )
        repo_automation.record_run(
            GH_CUT, status="launched", detail=detail, last_run_at=now, now=now
        )


# ---------------------------------------------------------------------------
# 3. Scheduled HF dataset batch-publish (owner timezone).
# ---------------------------------------------------------------------------


def eval_hf_publish(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.hf_publish
    if not c.enabled:
        return
    st = repo_automation.get_state(HF_PUBLISH)
    last_run = _serde.from_iso(st["last_run_at"]) if st else None
    if not is_due(
        last_run_at=last_run,
        interval_days=c.interval_days,
        time_of_day=c.time_of_day,
        timezone=c.timezone,
        now_utc=now,
    ):
        return
    if (
        jobs_base.running_job_for(kind="cut_release") is not None
        or jobs_base.running_job_for(kind="hf_publish_batch") is not None
        or jobs_base.running_job_for(kind="refresh_catalog") is not None
    ):
        return  # retry next tick
    slugs = _hf_publish_candidates()
    if not slugs:
        _record(HF_PUBLISH, status="skipped", detail="no candidates", last_run_at=now, now=now)
        return
    try:
        hf_publish_batch_jobs.launch(slugs, webhook_base=_webhook_base())
    except Exception as exc:  # noqa: BLE001
        logger.warning("hf_publish: batch launch failed: %s", exc)
        _record(HF_PUBLISH, status="error", detail=str(exc)[:200], now=now)
        return
    _record(
        HF_PUBLISH,
        status="launched",
        detail=f"batch-published {len(slugs)} recitation(s)",
        last_run_at=now,
        now=now,
    )


def _hf_publish_candidates() -> list[str]:
    """Fresh (``publish_hf``) + stale (``republish_hf``) HF candidates.

    Fresh = released, has a current TS release, never published to HF. Stale =
    has a current HF release stamped stale. Skips any slug with a job in flight.
    """
    in_flight = _in_flight_slugs(_ALL_JOB_KINDS)
    states = {
        r[0]: r[1] for r in get_conn().execute("SELECT slug, state FROM delivery_states").fetchall()
    }
    out: list[str] = []
    for slug in _delivery_slugs():
        if slug in in_flight:
            continue
        ts = repo_releases.current_release("ts", slug)
        hf = repo_releases.current_release("hf", slug)
        fresh = states.get(slug) == "released" and hf is None and ts is not None
        stale = hf is not None and bool(hf.get("stale_since"))
        if fresh or stale:
            out.append(slug)
    return out


# ---------------------------------------------------------------------------
# 4. Stale-TS regen (debounced once edits settle).
# ---------------------------------------------------------------------------


def eval_stale_ts_regen(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.stale_ts_regen
    if not c.enabled:
        return
    guard = timedelta(minutes=c.guard_minutes)
    failed = timestamps_jobs.latest_terminal_failed_slugs()
    in_flight = _in_flight_slugs(("timestamps",))
    watermarks = timestamps_jobs.latest_job_started_by_slug()
    launched: list[str] = []
    for slug in _delivery_slugs():
        if slug in failed or slug in in_flight:
            continue
        ts = repo_releases.current_release("ts", slug)
        if ts is None or not ts.get("produced_at"):
            continue
        info = ts_staleness.ts_stale_info(slug, produced_at=ts["produced_at"])
        if not info:
            continue
        last_edit = _serde.from_iso(info.get("last_edit_at"))
        if last_edit is None or (now - last_edit) < guard:
            continue  # edits still settling — debounce
        # Idempotency key: a regen we already launched for these edits clears
        # staleness only once its async completion lands (webhook / 120 s poll),
        # which lags the tick. Without this we re-fire every tick in that window —
        # and forever if completion never clears it. One job per edit-burst.
        if _already_launched_since(watermarks, slug, last_edit):
            continue
        chapters = info.get("affected_chapters") if c.scope == "affected" else None
        try:
            timestamps_jobs.launch(
                slug,
                settings=_ts_settings(cfg, chapters=chapters or None),
                webhook_base=_webhook_base(),
            )
            launched.append(slug)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stale_ts_regen: launch for %s failed: %s", slug, exc)
    if launched:
        _record(
            STALE_TS_REGEN,
            status="launched",
            detail="regenerated timestamps for " + ", ".join(launched),
            last_run_at=now,
            now=now,
        )


# ---------------------------------------------------------------------------
# 5. Stale-metadata refresh (debounced; one global refresh_catalog).
# ---------------------------------------------------------------------------


def eval_stale_metadata(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.stale_metadata
    if not c.enabled:
        return
    if (
        jobs_base.running_job_for(kind="refresh_catalog") is not None
        or jobs_base.running_job_for(kind="hf_publish_batch") is not None
    ):
        return
    guard = timedelta(minutes=c.guard_minutes)
    settled: list[str] = []
    for slug in _delivery_slugs():
        hf = repo_releases.current_release("hf", slug)
        if hf is None or hf.get("stale_reason") != "catalog_edit" or not hf.get("stale_since"):
            continue
        since = _serde.from_iso(hf["stale_since"])
        if since is None or (now - since) < guard:
            continue
        settled.append(slug)
    if not settled:
        return
    try:
        refresh_catalog_jobs.launch(webhook_base=_webhook_base())
    except Exception as exc:  # noqa: BLE001
        logger.warning("stale_metadata: refresh launch failed: %s", exc)
        _record(STALE_METADATA, status="error", detail=str(exc)[:200], now=now)
        return
    _record(
        STALE_METADATA,
        status="launched",
        detail=f"refreshed catalog ({len(settled)} stale)",
        last_run_at=now,
        now=now,
    )


# ---------------------------------------------------------------------------
# 6. Auto-release a reviewer's claim after prolonged inactivity.
# ---------------------------------------------------------------------------


def _inactive_claim_candidates() -> list[tuple[str, str]]:
    """``(slug, claimed_at)`` for every open, not-yet-marked-ready claim whose
    reciter is under review — the rows eligible for inactivity release.

    Marked-ready claims are excluded: those are complete and awaiting the
    pipeline (gen TS / publish), not an idle reviewer.
    """
    return [
        (r[0], r[1])
        for r in get_conn()
        .execute(
            "SELECT cl.slug, cl.claimed_at FROM claims cl "
            "JOIN delivery_states ds ON ds.slug = cl.slug "
            "WHERE cl.released_at IS NULL AND cl.marked_ready_at IS NULL "
            "AND ds.state = 'under_review' ORDER BY cl.slug"
        )
        .fetchall()
    ]


def _slug_last_edit_at(slug: str) -> datetime | None:
    """Latest edit-history save time for ``slug`` (reviewer-activity proxy).

    None when the reciter has no edit history (or it can't be read) — the caller
    then falls back to the claim time. An open claim is single-assignee under the
    edit-lock, so the newest save is effectively the reviewer's last action.
    """
    from services.activity.history_query import parse_history_for_reciter

    latest: datetime | None = None
    try:
        for batch in parse_history_for_reciter(slug):
            ts = _serde.from_iso(batch.get("saved_at_utc"))
            if ts is not None and (latest is None or ts > latest):
                latest = ts
    except Exception:  # noqa: BLE001 — missing/garbled history must not block the tick
        logger.warning("auto_release_inactive: edit-history read for %s failed", slug)
        return None
    return latest


def eval_auto_release_inactive(cfg: AutomationConfig, now: datetime) -> None:
    c = cfg.auto_release_inactive
    if not c.enabled:
        return
    from services.state import state as state_service

    cutoff = now - timedelta(days=c.inactive_days)
    released: list[str] = []
    for slug, claimed_at in _inactive_claim_candidates():
        last_active = _serde.from_iso(claimed_at)
        edit_at = _slug_last_edit_at(slug)
        if edit_at is not None and (last_active is None or edit_at > last_active):
            last_active = edit_at
        # Unknown activity → keep the claim (fail-safe). Active within the
        # window → not idle yet.
        if last_active is None or last_active >= cutoff:
            continue
        try:
            state_service.transition(
                slug,
                "claim.force_released",
                actor=system_actor(),
                reason=f"auto-released: reviewer inactive for {c.inactive_days}+ days",
            )
            released.append(slug)
        except Exception as exc:  # noqa: BLE001 — one slug's failure never blocks the rest
            logger.warning("auto_release_inactive: release for %s failed: %s", slug, exc)
    if released:
        _record(
            AUTO_RELEASE_INACTIVE,
            status="launched",
            detail="released inactive claim(s): " + ", ".join(released),
            last_run_at=now,
            now=now,
        )


#: The evaluators in execution order (cheap event-driven first, scheduled next).
EVALUATORS = (
    eval_shard_integrity,
    eval_auto_gen_ts,
    eval_stale_ts_regen,
    eval_stale_metadata,
    eval_auto_release_inactive,
    eval_hf_publish,
    eval_gh_cut,
)


def run_all(now: datetime) -> None:
    """Run every evaluator once against the current config; isolate failures."""
    cfg = load_config()
    for evaluator in EVALUATORS:
        try:
            evaluator(cfg, now)
        except Exception:  # noqa: BLE001 — one automation never kills the others
            logger.exception("automation: evaluator %s errored", evaluator.__name__)
