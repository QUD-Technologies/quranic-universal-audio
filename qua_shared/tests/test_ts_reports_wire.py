"""Native v12 report target and category policy tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from qua_shared.schemas.wire.ts_reports import (
    TsReportBatchCreateRequest,
    TsReportCreateRequest,
    TsReportSnapshot,
    timing_label,
)


def _target(kind: str, target_id: str = "1") -> dict:
    return {"reading_id": "r1", "kind": kind, "target_id": target_id}


def _request(**over) -> dict:
    base = {
        "verse_key": "2:45",
        "category": "other",
        "target": _target("verse", "2:45"),
        "comment": "detail",
    }
    return {**base, **over}


def test_target_requires_reading_kind_and_native_id():
    TsReportCreateRequest.model_validate(_request())
    for missing in ("reading_id", "kind", "target_id"):
        target = _target("word")
        target.pop(missing)
        with pytest.raises(ValidationError):
            TsReportCreateRequest.model_validate(_request(target=target))
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(_request(target={**_target("cell"), "kind": "cell"}))


@pytest.mark.parametrize("kind", ["word", "column", "sound", "group", "boundary", "bridge"])
def test_timing_accepts_every_native_timed_entity(kind: str):
    TsReportCreateRequest.model_validate(
        _request(category="timing", target=_target(kind), comment=None, onset="early")
    )


def test_timing_requires_an_axis_and_forbids_subtypes():
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(
            _request(category="timing", target=_target("word"), comment=None)
        )
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(
            _request(
                category="timing",
                target=_target("word"),
                comment=None,
                onset="early",
                subtype="wrong_rule",
            )
        )


@pytest.mark.parametrize("kind", ["column", "sound", "group", "bridge"])
def test_tajweed_accepts_native_rule_owners(kind: str):
    TsReportCreateRequest.model_validate(
        _request(
            category="tajweed",
            subtype="wrong_rule",
            target=_target(kind),
            selected_rule_tags=["qalqala_sughra"],
        )
    )


def test_phoneme_and_silence_targets_are_exact():
    for kind in ("sound", "bridge"):
        TsReportCreateRequest.model_validate(
            _request(category="phonemes", target=_target(kind), comment=None)
        )
    TsReportCreateRequest.model_validate(
        _request(
            category="silence",
            subtype="pause_missed",
            target=_target("boundary"),
            comment=None,
        )
    )
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(
            _request(
                category="silence",
                subtype="pause_missed",
                target=_target("word"),
                comment=None,
            )
        )


def test_comment_and_selected_rule_policy_is_enforced():
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(_request(comment=" "))
    with pytest.raises(ValidationError):
        TsReportCreateRequest.model_validate(
            _request(
                category="tajweed",
                subtype="missing_rule",
                target=_target("column"),
                selected_rule_tags=["qalqala_sughra"],
            )
        )


def test_batch_reuses_the_single_item_policy():
    request = TsReportBatchCreateRequest.model_validate(
        {
            "verse_key": "2:45",
            "items": [
                {"category": "timing", "target": _target("word"), "onset": "late"},
                {
                    "category": "tajweed",
                    "subtype": "missing_rule",
                    "target": _target("column"),
                    "comment": "missing",
                },
            ],
        }
    )
    assert len(request.items) == 2
    with pytest.raises(ValidationError):
        TsReportBatchCreateRequest.model_validate({"verse_key": "2:45", "items": []})


def test_snapshot_is_schema_guarded():
    snapshot = TsReportSnapshot.model_validate(
        {
            "native_schema_version": 2,
            "shard_schema_version": 12,
            "native": {"id": 1},
            "timing": {"start_ms": 10, "end_ms": 20},
        }
    )
    assert snapshot.native == {"id": 1}
    v15 = TsReportSnapshot.model_validate({"shard_schema_version": 15, "native": {}})
    assert v15.shard_schema_version == 15
    with pytest.raises(ValidationError):
        TsReportSnapshot.model_validate(
            {"native_schema_version": 1, "shard_schema_version": 12, "native": {}}
        )


def test_timing_label_matrix():
    assert timing_label("early", "late") == "Too long"
    assert timing_label("late", "early") == "Too short"
    assert timing_label("early", "early") == "Shifted earlier"
    assert timing_label("late", "late") == "Shifted later"
    assert timing_label("early", None) == "Starts early"
    assert timing_label(None, "late") == "Finishes late"
