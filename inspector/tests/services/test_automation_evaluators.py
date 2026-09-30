"""Automation evaluators — config cache, auto-gen gates, scheduled guards.

The job launchers + ``list_jobs`` are monkeypatched on the ``evaluators`` module
so no real HF call fires; assertions read the durable ``automation_state`` row
back rather than trusting the spy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qua_shared.schemas import (
    AutoGenTsConfig,
    AutomationConfig,
    AutoReleaseInactiveConfig,
    GhCutConfig,
    HfPublishConfig,
    StaleTsRegenConfig,
)
from services import db
from services.admin.automation import config as automation_config
from services.admin.automation import evaluators
from services.db import repo_access, repo_automation, repo_claims, repo_notifications
from services.state import state as state_service
from services.storage import cache


@pytest.fixture(autouse=True)
def _clear_automation_cache():
    """The config cache is db_seq-keyed and the seq counter resets per test —
    clear it so a prior test's cached config doesn't bleed in."""
    cache.invalidate_automation_config_cache()
    yield
    cache.invalidate_automation_config_cache()


def _now() -> datetime:
    return datetime(2026, 6, 9, 12, 0, tzinfo=UTC)


# --- config load/save + cache ------------------------------------------------


def test_load_config_defaults_then_reflects_save():
    assert automation_config.load_config().gh_cut.enabled is False
    automation_config.save_config(
        AutomationConfig(gh_cut=GhCutConfig(enabled=True)), updated_by="owner"
    )
    assert automation_config.load_config().gh_cut.enabled is True


def test_unparseable_stored_blob_degrades_to_defaults():
    from services.db import transaction

    with transaction():
        repo_automation.set_config_json("not valid json", updated_by="owner")
    cache.invalidate_automation_config_cache()
    cfg = automation_config.load_config()
    assert cfg.gh_cut.enabled is False  # defaults, no crash


def test_reads_tolerate_missing_tables():
    """A DB whose 0020 migration hasn't applied must degrade to defaults — not
    500 the Releases tab / crash the reconciler tick."""
    from services.db import transaction

    with transaction() as conn:
        conn.execute("DROP TABLE automation_config")
        conn.execute("DROP TABLE automation_state")
    cache.invalidate_automation_config_cache()

    assert repo_automation.get_config_json() is None
    assert repo_automation.all_state() == []
    assert repo_automation.get_state("gh_cut") is None
    assert automation_config.load_config().gh_cut.enabled is False  # defaults, no raise


# --- auto-gen gates ----------------------------------------------------------


def _clean_claim() -> dict:
    return {
        "mark_ready_bypass_used": 0,
        "mark_ready_comment_checks": "",
        "mark_ready_comment_issues": "",
    }


def test_auto_gen_not_gated_when_clean(monkeypatch):
    monkeypatch.setattr(evaluators, "load_detailed", lambda slug: [])
    monkeypatch.setattr(evaluators, "count_flagged", lambda entries: 0)
    c = AutoGenTsConfig(enabled=True)
    assert evaluators._auto_gen_gated(c, "slug", _clean_claim()) is False


def test_auto_gen_not_gated_on_owner_checklist_bypass(monkeypatch):
    # Bypass is owner-only ("vetted, no checklist needed") → it must NOT block
    # auto-gen. Only the owner-configured comment/flag gates do.
    monkeypatch.setattr(evaluators, "load_detailed", lambda slug: [])
    monkeypatch.setattr(evaluators, "count_flagged", lambda entries: 0)
    claim = _clean_claim() | {"mark_ready_bypass_used": 1}
    assert evaluators._auto_gen_gated(AutoGenTsConfig(enabled=True), "slug", claim) is False


def test_auto_gen_gated_on_reviewer_comment(monkeypatch):
    monkeypatch.setattr(evaluators, "load_detailed", lambda slug: [])
    monkeypatch.setattr(evaluators, "count_flagged", lambda entries: 0)
    claim = _clean_claim() | {"mark_ready_comment_issues": "boundary looks off"}
    assert evaluators._auto_gen_gated(AutoGenTsConfig(enabled=True), "slug", claim) is True


def test_auto_gen_gated_on_flagged_segment(monkeypatch):
    monkeypatch.setattr(evaluators, "load_detailed", lambda slug: [{"segments": []}])
    monkeypatch.setattr(evaluators, "count_flagged", lambda entries: 3)
    assert evaluators._auto_gen_gated(AutoGenTsConfig(enabled=True), "slug", _clean_claim()) is True


def test_auto_gen_comment_gate_off_ignores_comment(monkeypatch):
    monkeypatch.setattr(evaluators, "load_detailed", lambda slug: [])
    monkeypatch.setattr(evaluators, "count_flagged", lambda entries: 0)
    c = AutoGenTsConfig(enabled=True, gate_by_comments=False)
    claim = _clean_claim() | {"mark_ready_comment_issues": "noted"}
    assert evaluators._auto_gen_gated(c, "slug", claim) is False


# --- scheduled guards --------------------------------------------------------


def test_gh_cut_skips_empty_cut_and_advances_cadence(monkeypatch):
    launched: list[dict] = []
    monkeypatch.setattr(evaluators, "is_due", lambda **k: True)
    monkeypatch.setattr(evaluators, "current_auto_version", lambda: (None, 0))
    monkeypatch.setattr(evaluators.jobs_base, "running_job_for", lambda **k: None)
    monkeypatch.setattr(evaluators.cut_release_jobs, "launch", lambda **k: launched.append(k))

    evaluators.eval_gh_cut(AutomationConfig(gh_cut=GhCutConfig(enabled=True)), _now())

    assert launched == []  # no cut fired for an empty release
    st = repo_automation.get_state("gh_cut")
    assert st is not None
    assert st["last_status"] == "skipped"
    assert st["last_run_at"] is not None  # cadence advanced so we don't re-check every tick


def test_gh_cut_launches_when_changes_exist(monkeypatch):
    launched: list[dict] = []
    monkeypatch.setattr(evaluators, "is_due", lambda **k: True)
    monkeypatch.setattr(evaluators, "current_auto_version", lambda: ("v1.2.0", 3))
    monkeypatch.setattr(evaluators.jobs_base, "running_job_for", lambda **k: None)
    monkeypatch.setattr(evaluators.cut_release_jobs, "launch", lambda **k: launched.append(k))

    evaluators.eval_gh_cut(AutomationConfig(gh_cut=GhCutConfig(enabled=True)), _now())

    assert len(launched) == 1
    gh_cut_state = repo_automation.get_state("gh_cut")
    assert gh_cut_state is not None
    assert gh_cut_state["last_status"] == "launched"


def test_gh_cut_one_shot_override_is_cleared_after_launch(monkeypatch):
    launched: list[dict] = []
    monkeypatch.setattr(evaluators, "is_due", lambda **k: True)
    monkeypatch.setattr(evaluators, "current_auto_version", lambda: ("v1.2.0", 3))
    monkeypatch.setattr(evaluators.jobs_base, "running_job_for", lambda **k: None)
    monkeypatch.setattr(evaluators.cut_release_jobs, "launch", lambda **k: launched.append(k))

    cfg = AutomationConfig(gh_cut=GhCutConfig(enabled=True, next_version_override="v9.9.9"))
    evaluators.eval_gh_cut(cfg, _now())

    assert launched[0]["version"] == "v9.9.9"  # override passed to the cut
    cache.invalidate_automation_config_cache()
    assert automation_config.load_config().gh_cut.next_version_override is None  # cleared


def test_hf_publish_skips_when_no_candidates(monkeypatch):
    launched: list[list[str]] = []
    monkeypatch.setattr(evaluators, "is_due", lambda **k: True)
    monkeypatch.setattr(evaluators.jobs_base, "running_job_for", lambda **k: None)
    monkeypatch.setattr(evaluators.jobs_base, "list_in_flight_jobs", lambda kinds: [])
    monkeypatch.setattr(
        evaluators.hf_publish_batch_jobs, "launch", lambda slugs, **k: launched.append(slugs)
    )

    # No deliveries seeded → no fresh/stale candidates.
    evaluators.eval_hf_publish(AutomationConfig(hf_publish=HfPublishConfig(enabled=True)), _now())

    assert launched == []
    st = repo_automation.get_state("hf_publish")
    assert st is not None
    assert st["last_status"] == "skipped"


# --- stale-TS regen: relaunch watermark (the infinite-jobs guard) ------------


def _seed_stale_slug(monkeypatch, *, last_edit: str, watermark: dict[str, datetime]):
    """Wire stale_ts_regen's reads so 'rec_a' is past-guard stale, with a given
    last_edit watermark + per-slug newest-job start map."""
    monkeypatch.setattr(evaluators, "_delivery_slugs", lambda: ["rec_a"])
    monkeypatch.setattr(evaluators, "_in_flight_slugs", lambda kinds: set())
    monkeypatch.setattr(evaluators.timestamps_jobs, "latest_terminal_failed_slugs", lambda: set())
    monkeypatch.setattr(evaluators.timestamps_jobs, "latest_job_started_by_slug", lambda: watermark)
    monkeypatch.setattr(
        evaluators.repo_releases,
        "current_release",
        lambda track, slug: {"produced_at": "2026-06-09T08:00:00Z"},
    )
    monkeypatch.setattr(
        evaluators.ts_staleness,
        "ts_stale_info",
        lambda slug, produced_at: {
            "stale_since": last_edit,
            "last_edit_at": last_edit,
            "edits_since": 1,
            "affected_chapters": [2],
        },
    )


def test_stale_ts_regen_skips_when_a_job_already_covers_these_edits(monkeypatch):
    """The infinite-loop guard: a regen whose newest job started AFTER the last
    edit is already pending — staleness clears only when its async completion
    lands — so the tick must NOT re-fire."""
    launched: list[str] = []
    _seed_stale_slug(
        monkeypatch,
        last_edit="2026-06-09T10:00:00Z",
        watermark={"rec_a": datetime(2026, 6, 9, 11, 0, tzinfo=UTC)},  # job AFTER the edit
    )
    monkeypatch.setattr(
        evaluators.timestamps_jobs, "launch", lambda slug, **k: launched.append(slug)
    )

    evaluators.eval_stale_ts_regen(
        AutomationConfig(stale_ts_regen=StaleTsRegenConfig(enabled=True)), _now()
    )

    assert launched == []  # no duplicate job while completion settles
    assert repo_automation.get_state("stale_ts_regen") is None  # nothing acted → no write


def test_stale_ts_regen_launches_when_edits_are_newer_than_last_job(monkeypatch):
    """A genuinely newer edit (after the last job started) is real new staleness
    → regen fires exactly once."""
    launched: list[str] = []
    _seed_stale_slug(
        monkeypatch,
        last_edit="2026-06-09T10:00:00Z",
        watermark={"rec_a": datetime(2026, 6, 9, 9, 0, tzinfo=UTC)},  # last job BEFORE the edit
    )
    monkeypatch.setattr(
        evaluators.timestamps_jobs, "launch", lambda slug, **k: launched.append(slug)
    )

    evaluators.eval_stale_ts_regen(
        AutomationConfig(stale_ts_regen=StaleTsRegenConfig(enabled=True)), _now()
    )

    assert launched == ["rec_a"]
    stale_ts_state = repo_automation.get_state("stale_ts_regen")
    assert stale_ts_state is not None
    assert stale_ts_state["last_status"] == "launched"


def test_stale_ts_regen_waits_while_any_timestamps_run_is_in_flight(monkeypatch):
    """The timing Space runs one at a time: another reciter's run blocks launching."""
    launched: list[str] = []
    _seed_stale_slug(monkeypatch, last_edit="2026-06-09T10:00:00Z", watermark={})
    monkeypatch.setattr(evaluators, "_in_flight_slugs", lambda kinds: {"rec_other"})
    monkeypatch.setattr(
        evaluators.timestamps_jobs, "launch", lambda slug, **k: launched.append(slug)
    )

    evaluators.eval_stale_ts_regen(
        AutomationConfig(stale_ts_regen=StaleTsRegenConfig(enabled=True)), _now()
    )

    assert launched == []


def test_stale_ts_regen_launches_only_the_longest_stale(monkeypatch):
    launched: list[tuple[str, list[int] | None]] = []
    _seed_stale_slug(monkeypatch, last_edit="2026-06-09T10:00:00Z", watermark={})
    monkeypatch.setattr(evaluators, "_delivery_slugs", lambda: ["rec_a", "rec_b", "rec_c"])
    since = {
        "rec_a": "2026-06-09T10:00:00Z",
        "rec_b": "2026-06-09T08:00:00Z",
        "rec_c": "2026-06-09T09:00:00Z",
    }
    monkeypatch.setattr(
        evaluators.ts_staleness,
        "ts_stale_info",
        lambda slug, produced_at: {
            "stale_since": since[slug],
            "last_edit_at": since[slug],
            "edits_since": 1,
            "affected_chapters": [2],
        },
    )
    monkeypatch.setattr(
        evaluators.timestamps_jobs,
        "launch",
        lambda slug, settings, **k: launched.append((slug, settings.chapters)),
    )

    evaluators.eval_stale_ts_regen(
        AutomationConfig(stale_ts_regen=StaleTsRegenConfig(enabled=True)), _now()
    )

    assert launched == [("rec_b", [2])]
    state = repo_automation.get_state("stale_ts_regen")
    assert state is not None and "2 waiting" in state["last_detail"]


# --- auto-release inactive claims -------------------------------------------


def _release_cfg(*, days: int = 14) -> AutomationConfig:
    return AutomationConfig(
        auto_release_inactive=AutoReleaseInactiveConfig(enabled=True, inactive_days=days)
    )


def test_auto_release_inactive_disabled_records_nothing():
    evaluators.eval_auto_release_inactive(AutomationConfig(), _now())
    assert repo_automation.get_state(evaluators.AUTO_RELEASE_INACTIVE) is None


def test_auto_release_inactive_fires_force_release_for_idle_claim(monkeypatch):
    """Claimed long ago, no segment edits → reviewer is idle → release fires once."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        evaluators, "_inactive_claim_candidates", lambda: [("rec_a", "2026-04-01T00:00:00Z")]
    )
    monkeypatch.setattr(evaluators, "_slug_last_edit_at", lambda s: None)
    monkeypatch.setattr(
        state_service, "transition", lambda slug, event, **k: calls.append((slug, event))
    )

    evaluators.eval_auto_release_inactive(_release_cfg(), _now())

    assert calls == [("rec_a", "claim.force_released")]
    release_state = repo_automation.get_state(evaluators.AUTO_RELEASE_INACTIVE)
    assert release_state is not None
    assert release_state["last_status"] == "launched"


def test_auto_release_inactive_keeps_claim_with_recent_edit(monkeypatch):
    """A reviewer editing within the window is active even if they claimed long
    ago — the latest edit, not the claim time, decides."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        evaluators, "_inactive_claim_candidates", lambda: [("rec_a", "2026-04-01T00:00:00Z")]
    )
    monkeypatch.setattr(
        evaluators, "_slug_last_edit_at", lambda s: datetime(2026, 6, 8, tzinfo=UTC)
    )
    monkeypatch.setattr(state_service, "transition", lambda *a, **k: calls.append(a))

    evaluators.eval_auto_release_inactive(_release_cfg(), _now())

    assert calls == []  # active within the window → not released
    assert repo_automation.get_state(evaluators.AUTO_RELEASE_INACTIVE) is None


def test_inactive_candidates_exclude_marked_ready_and_unclaimed(seed_state):
    """Only open, not-yet-marked-ready under-review claims are eligible."""
    seed_state("idle1", state="under_review", assignee_hf_id="u1", assignee_login="rev1")
    seed_state(
        "ready1",
        state="under_review",
        assignee_hf_id="u2",
        assignee_login="rev2",
        marked_ready=True,
    )
    seed_state("awaiting1", state="awaiting_review")  # no open claim

    cands = {slug for slug, _ in evaluators._inactive_claim_candidates()}

    assert cands == {"idle1"}


def test_auto_release_inactive_releases_real_claim_and_notifies(seed_state, monkeypatch):
    """End-to-end: an idle claim is force-released through the real transition,
    returns to the pool, and the reviewer gets the same notification the manual
    release path emits."""
    slug = "idle_rec"
    seed_state(slug, state="under_review")  # state + FK chain, claim opened next
    with db.transaction():
        repo_access.ensure_user("rev1", login="reviewer_one")
        repo_claims.open_claim(
            slug=slug,
            assignee_id="rev1",
            assignee_login="reviewer_one",
            claimed_at=datetime(2026, 5, 10, tzinfo=UTC),  # ~30d before _now()
        )
    monkeypatch.setattr(evaluators, "_slug_last_edit_at", lambda s: None)  # no edits

    evaluators.eval_auto_release_inactive(_release_cfg(days=14), _now())

    assert repo_claims.get_open_claim(slug) is None  # claim released
    released_row = state_service.get_row(slug)
    assert released_row is not None
    assert released_row.state.value == "awaiting_review"
    events = {n["event"] for n in repo_notifications.list_active("rev1")}
    assert "claim.force_released" in events
    release_state = repo_automation.get_state(evaluators.AUTO_RELEASE_INACTIVE)
    assert release_state is not None
    assert release_state["last_status"] == "launched"


def test_auto_release_inactive_keeps_recently_claimed_real_row(seed_state, monkeypatch):
    """A freshly-claimed row stays put — the durable claim is untouched."""
    slug = "fresh_rec"
    seed_state(slug, state="under_review")
    with db.transaction():
        repo_access.ensure_user("rev2", login="reviewer_two")
        repo_claims.open_claim(
            slug=slug,
            assignee_id="rev2",
            assignee_login="reviewer_two",
            claimed_at=_now() - timedelta(days=2),  # well within the window
        )
    monkeypatch.setattr(evaluators, "_slug_last_edit_at", lambda s: None)

    evaluators.eval_auto_release_inactive(_release_cfg(days=14), _now())

    assert repo_claims.get_open_claim(slug) is not None  # still claimed
    claimed_row = state_service.get_row(slug)
    assert claimed_row is not None
    assert claimed_row.state.value == "under_review"
