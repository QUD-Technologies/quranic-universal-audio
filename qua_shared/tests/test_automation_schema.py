"""AutomationConfig schema — defaults, validation, forward-compat round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qua_shared.schemas import AutoGenTsConfig, AutomationConfig, GhCutConfig


def test_defaults_are_all_disabled_with_shared_beam_defaults():
    cfg = AutomationConfig()
    assert cfg.auto_gen_ts.enabled is False
    assert cfg.gh_cut.enabled is False
    assert cfg.hf_publish.enabled is False
    assert cfg.stale_ts_regen.enabled is False
    assert cfg.stale_metadata.enabled is False
    assert cfg.auto_release_inactive.enabled is False
    # The TS tunables live in the shared defaults, not per-automation.
    assert cfg.ts_generation_defaults.beam == 50
    assert "probe_beams" not in type(cfg.ts_generation_defaults).model_fields
    assert cfg.gh_cut.interval_days == 7
    assert cfg.gh_cut.time_of_day == "09:00"
    assert cfg.gh_cut.timezone == "Australia/Sydney"
    assert cfg.stale_ts_regen.scope == "affected"
    assert cfg.auto_release_inactive.inactive_days == 14


def test_auto_release_inactive_days_must_be_positive():
    from qua_shared.schemas import AutoReleaseInactiveConfig

    with pytest.raises(ValidationError):
        AutoReleaseInactiveConfig(inactive_days=0)


def test_unknown_top_level_key_round_trips_for_forward_compat():
    # extra="allow" keeps a newer FE's unknown automation key through a re-dump.
    cfg = AutomationConfig.model_validate(
        {"gh_cut": {"enabled": True}, "future_automation": {"x": 1}}
    )
    assert cfg.gh_cut.enabled is True
    dumped = cfg.model_dump()
    assert dumped["future_automation"] == {"x": 1}


def test_old_blob_with_retired_per_automation_ts_keys_loads_clean():
    # Migration-safety: existing saved blobs carry the retired per-automation
    # beam/probe_beams/aligner_model keys. extra="allow" on the sub-models must
    # tolerate them (validate clean, ignored — the shared defaults own them now).
    cfg = AutomationConfig.model_validate(
        {
            "auto_gen_ts": {"enabled": True, "beam": 80, "probe_beams": 3, "aligner_model": "m1"},
            "stale_ts_regen": {"enabled": True, "beam": 40, "probe_beams": 2},
        }
    )
    assert cfg.auto_gen_ts.enabled is True
    assert cfg.stale_ts_regen.enabled is True
    # The retired keys do not surface as model fields.
    assert "beam" not in AutoGenTsConfig.model_fields
    assert "aligner_model" not in AutoGenTsConfig.model_fields


def test_blank_next_version_override_normalizes_to_none():
    cfg = AutomationConfig(gh_cut=GhCutConfig(next_version_override="   "))
    assert cfg.gh_cut.next_version_override is None
    kept = AutomationConfig(gh_cut=GhCutConfig(next_version_override="v1.2.3"))
    assert kept.gh_cut.next_version_override == "v1.2.3"


def test_json_round_trip_preserves_settings():
    cfg = AutomationConfig(
        auto_gen_ts=AutoGenTsConfig(enabled=True, gate_by_flags=False, gate_by_comments=False),
    )
    back = AutomationConfig.model_validate_json(cfg.model_dump_json())
    assert back.auto_gen_ts.enabled is True
    assert back.auto_gen_ts.gate_by_flags is False
    assert back.auto_gen_ts.gate_by_comments is False


@pytest.mark.parametrize("bad", ["9:00", "25:00", "09:60", "0900", ""])
def test_bad_time_of_day_is_rejected(bad):
    with pytest.raises(ValidationError):
        GhCutConfig(time_of_day=bad)


def test_out_of_range_numeric_constraints_are_rejected():
    from qua_shared.schemas import TsGenerationDefaults

    with pytest.raises(ValidationError):
        TsGenerationDefaults(beam=0)
    with pytest.raises(ValidationError):
        GhCutConfig(interval_days=0)
