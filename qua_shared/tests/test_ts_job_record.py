"""TsJobSettings / TsJobRecord schema tests.

``TsJobSettings`` ignores unknown keys: run records written by the MFA engine
carry its alignment knobs, which parse and are dropped. A record round-trips.
"""

from __future__ import annotations

from qua_shared.schemas import TsJobRecord, TsJobSettings


def test_settings_drop_mfa_alignment_knobs():
    s = TsJobSettings.model_validate(
        {
            "beams": [50, 2],
            "workers": 8,
            "aligner_model": "m1",
            "persist_audio": True,
            "chapters": [2],
        }
    )
    assert s.model_dump() == {"chapters": [2]}


def test_mfa_record_validates_and_keeps_its_scope():
    rec = TsJobRecord.model_validate(
        {
            "job_id": "j1",
            "slug": "minshawy_murattal",
            "settings": {"beams": [50], "padding": "forward", "chapters": [108]},
            "status": "succeeded",
        }
    )
    assert rec.settings.chapters == [108]
    assert rec.model_dump()["settings"] == {"chapters": [108]}


def test_record_round_trips():
    rec = TsJobRecord(
        job_id="j1", slug="r", settings=TsJobSettings(chapters=[1, 2]), status="failed"
    )
    assert TsJobRecord.model_validate_json(rec.model_dump_json()) == rec


def test_settings_default_is_full_reciter():
    assert TsJobSettings().chapters is None
