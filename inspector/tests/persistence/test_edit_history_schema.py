"""Round-trip + rejection tests for the shared ``EditHistoryBatch`` /
``EditOperation`` schema under pure ``extra="forbid"``.

Two fields that the live save flow WRITES and the undo / resolved-by-edit
paths READ — ``patch`` and ``op_context_category`` — are declared fields
(``patch`` typed as ``EditOpPatch``) and must survive a round-trip.

There is no longer a legacy-tolerance layer: ``save_mode`` (a wire-only
presentation hint the save flow no longer persists), the file-hash chain,
and the genuinely-dead v0 op fields (per-op timestamps, ``affected_chapters``,
``value`` / ``field`` / ``op``) are all REJECTED on read — any unexpected key
raises ``ValidationError``. The FE user-edit working fields (``type`` /
``command`` / ``snapshots`` / ``targetSegmentIndex`` / ``merge_direction``)
ARE declared and round-trip, because the Inspector save flow persists them
verbatim. The History-panel wire shape still re-derives ``save_mode`` for
display.

The committed ``112-ikhlas.edit_history.jsonl`` fixture deliberately carries
legacy keys; it is read raw by the save-flow tests and used here only as a
rejection case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qua_shared.schemas import EditOperation, EditOpPatch, parse_edit_history_line

_FIXTURE = (
    Path(__file__).parents[2] / "tests" / "fixtures" / "segments" / "112-ikhlas.edit_history.jsonl"
)


def _fixture_lines() -> list[str]:
    return [ln for ln in _FIXTURE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _clean_batch_line() -> str:
    """A clean batch line in the canonical live-save shape — one op carrying a
    real ``patch`` + ``op_context_category``, no retired keys."""
    return json.dumps(
        {
            "schema_version": 1,
            "batch_id": "clean-batch-1",
            "chapter": 112,
            "saved_at_utc": "2026-06-10T00:00:00.000Z",
            "operations": [
                {
                    "op_id": "clean-op-1",
                    "op_type": "trim_segment",
                    "op_context_category": "repetitions",
                    "patch": {
                        "before": [
                            {
                                "segment_uid": "019d5c88-f55f-7ee0-81d1-d99f423e8dd5",
                                "time_start": 0,
                                "time_end": 1000,
                                "matched_ref": "112:1",
                            }
                        ],
                        "after": [],
                        "removedIds": [],
                        "insertedIds": [],
                        "affectedChapterIds": [112],
                    },
                }
            ],
        }
    )


# -- (a) live fields survive ------------------------------------------------


def test_clean_batch_patch_and_context_survive_round_trip():
    """A clean batch carrying a real ``patch`` + ``op_context_category`` on its
    op: both must survive ``parse_edit_history_line`` → ``model_dump`` intact."""
    batch = parse_edit_history_line(_clean_batch_line())
    assert batch is not None
    assert len(batch.operations) == 1
    op = batch.operations[0]

    assert isinstance(op.patch, EditOpPatch)
    assert op.patch.before and op.patch.before[0]["segment_uid"] == (
        "019d5c88-f55f-7ee0-81d1-d99f423e8dd5"
    )
    assert op.patch.affectedChapterIds == [112]

    dumped = batch.model_dump(exclude_none=True)
    dumped_op = dumped["operations"][0]
    assert "patch" in dumped_op, "patch was dropped on round-trip"
    assert dumped_op["patch"]["affectedChapterIds"] == [112]
    assert dumped_op["patch"]["before"][0]["segment_uid"] == (
        "019d5c88-f55f-7ee0-81d1-d99f423e8dd5"
    )


def test_committed_legacy_fixture_rejected():
    """The committed fixture carries retired batch + op keys (``save_mode``,
    ``file_hash_after``, ``reciter`` at batch level; ``applied_at_utc`` /
    ``ready_at_utc`` / ``started_at_utc`` per op). Under pure ``extra="forbid"``
    ``parse_edit_history_line`` now raises ``ValidationError`` instead of
    stripping them."""
    [line] = _fixture_lines()
    with pytest.raises(ValueError):
        parse_edit_history_line(line)


def test_op_context_category_survives_round_trip():
    """``op_context_category`` (read by build_resolved_by_edit_index) is a
    declared field now — it must NOT be stripped."""
    op = EditOperation.model_validate(
        {
            "op_id": "op-ctx-1",
            "op_type": "trim_segment",
            "op_context_category": "repetitions",
            "targets_before": [],
            "targets_after": [],
        }
    )
    assert op.op_context_category == "repetitions"
    out = op.model_dump(exclude_none=True)
    assert out["op_context_category"] == "repetitions"
    assert (op.model_extra or {}) == {}


# -- (b) freshly-built batch does not persist save_mode ---------------------


def test_fresh_batch_model_dump_has_no_save_mode():
    """A batch built the way the save flow builds it (no ``save_mode`` key)
    must NOT gain one through the schema — it's a wire-only hint, not a
    persisted field."""
    fresh = {
        "schema_version": 1,
        "batch_id": "fresh-batch-1",
        "chapter": 112,
        "saved_at_utc": "2026-06-10T00:00:00.000Z",
        "operations": [
            {
                "op_id": "fresh-op-1",
                "op_type": "trim_segment",
                "op_context_category": "repetitions",
                "patch": {
                    "before": [],
                    "after": [],
                    "removedIds": [],
                    "insertedIds": [],
                    "affectedChapterIds": [112],
                },
            }
        ],
    }
    batch = parse_edit_history_line(json.dumps(fresh))
    assert batch is not None
    dumped = batch.model_dump(exclude_none=True)
    assert "save_mode" not in dumped, "save_mode leaked into persisted batch shape"


def test_batch_with_save_mode_rejected():
    """``save_mode`` is a wire-only hint, never a persisted batch field — a
    batch carrying it is REJECTED under pure ``extra="forbid"`` rather than
    parsed-and-stripped."""
    obj = json.loads(_clean_batch_line())
    obj["save_mode"] = "full_replace"
    with pytest.raises(ValueError):
        parse_edit_history_line(json.dumps(obj))


# -- (c) genesis lines → None ----------------------------------------------


def test_genesis_lines_parse_to_none():
    """v0 (``type=="genesis"``), v1 (``record_type=="genesis"``), and a
    structurally-pre-batch_id record all parse to ``None``."""
    assert parse_edit_history_line('{"type": "genesis", "audio_source": "qul"}') is None
    assert parse_edit_history_line('{"record_type": "genesis", "extraction_params": {}}') is None
    assert parse_edit_history_line('{"saved_at_utc": "2026-01-01T00:00:00Z"}') is None
    assert parse_edit_history_line("") is None
    assert parse_edit_history_line("   ") is None


# -- (d) genuinely-dead op fields are rejected ------------------------------


def test_dead_op_field_rejected():
    """Genuinely-dead v0 op fields the live writers NEVER emit (per-op
    timestamps + the v1 pipeline vocab) still raise under pure
    ``extra="forbid"``. The FE working fields (``type`` / ``command`` /
    ``snapshots`` / ``targetSegmentIndex`` / ``merge_direction``) are NOT in
    this set — they are declared (see ``test_user_edit_op_round_trips``)."""
    for dead in (
        "applied_at_utc",
        "ready_at_utc",
        "started_at_utc",
        "affected_chapters",
        "value",
        "field",
        "op",
    ):
        with pytest.raises(ValueError):
            EditOperation.model_validate(
                {
                    "op_id": "dead-op-1",
                    "op_type": "trim_segment",
                    "op_context_category": "repetitions",
                    "targets_before": [],
                    "targets_after": [],
                    dead: "x",
                }
            )


def test_user_edit_op_round_trips():
    """A user-edit op shaped exactly as the FE ``apply-command.ts::
    _baseOperation`` emits it — and persisted verbatim by the Inspector save
    flow — round-trips under pure ``extra="forbid"``. This guards the
    writer/schema drift class: the FE stamps ``type`` / ``kind`` /
    ``snapshots`` / ``targetSegmentIndex`` / ``command`` (+ ``merge_direction``
    on merges) on every op, and the save path appends them as-is, so the
    schema MUST declare them or the bucket validator / ``/healthz?deep=1``
    reject every freshly-saved reciter."""
    op = EditOperation.model_validate(
        {
            "op_id": "user-merge-1",
            "op_type": "merge_segments",
            "kind": "structural",
            "op_context_category": "audio_bleeding",
            "fix_kind": "manual",
            "type": "merge",
            "merge_direction": "prev",
            "snapshots": {
                "before": [{"segment_uid": "a"}, {"segment_uid": "b"}],
                "after": [{"segment_uid": "a"}],
            },
            "targetSegmentIndex": {"chapter": 112, "index": 1},
            "command": {"type": "merge", "segmentUid": "b", "direction": "prev"},
            "targets_before": [{"segment_uid": "a"}, {"segment_uid": "b"}],
            "targets_after": [{"segment_uid": "a"}],
            "patch": {
                "before": [],
                "after": [],
                "removedIds": ["b"],
                "insertedIds": [],
                "affectedChapterIds": [112],
            },
        }
    )
    assert op.merge_direction == "prev"
    assert op.snapshots is not None and op.command is not None
    assert (op.model_extra or {}) == {}  # nothing landed in extras
    # And it survives a full JSONL batch round-trip through the reader.
    batch = parse_edit_history_line(
        json.dumps(
            {
                "schema_version": 1,
                "batch_id": "user-batch-1",
                "chapter": 112,
                "saved_at_utc": "2026-06-10T00:00:00.000Z",
                "operations": [op.model_dump(exclude_none=True)],
            }
        )
    )
    assert batch is not None
    assert batch.operations[0].merge_direction == "prev"


def test_clean_op_validates_with_promoted_fields():
    """A clean op (no retired keys) validates and keeps its live promoted
    fields — ``op_context_category`` present, ``patch`` defaulting to None."""
    op = EditOperation.model_validate(
        {
            "op_id": "clean-op-2",
            "op_type": "trim_segment",
            "op_context_category": "repetitions",
            "targets_before": [],
            "targets_after": [],
        }
    )
    assert op.op_context_category == "repetitions"
    assert op.patch is None
    assert (op.model_extra or {}) == {}


# -- WIRE: _load_edit_history_from_records derives save_mode + is_revert -----


def test_wire_history_batch_derives_save_mode_and_is_revert():
    """Feed records through the History-panel wire builder and assert the
    emitted batch dict still carries a derived ``save_mode`` (the save flow no
    longer persists it) plus ``is_revert``."""
    from services.activity.history_query import _load_edit_history_from_records

    records = [
        {
            "schema_version": 1,
            "batch_id": "wire-batch-1",
            "chapter": 112,
            "saved_at_utc": "2026-06-10T00:00:00.000Z",
            # No save_mode persisted — must be derived. A merge op → structural.
            "operations": [
                {
                    "op_id": "wire-op-1",
                    "op_type": "merge_segments",
                    "op_context_category": "repetitions",
                    "targets_before": [],
                    "targets_after": [],
                }
            ],
        },
        {
            "schema_version": 1,
            "batch_id": "wire-batch-2",
            "chapter": 112,
            "saved_at_utc": "2026-06-10T00:01:00.000Z",
            # Field-only edit → patch.
            "operations": [
                {
                    "op_id": "wire-op-2",
                    "op_type": "edit_reference",
                    "targets_before": [],
                    "targets_after": [],
                }
            ],
        },
    ]

    result = _load_edit_history_from_records("wire_reciter", records, cache_result=False)
    batches = {b["batch_id"]: b for b in result["batches"]}

    assert batches["wire-batch-1"]["save_mode"] == "full_replace"
    assert batches["wire-batch-1"]["is_revert"] is False
    assert batches["wire-batch-2"]["save_mode"] == "patch"
    assert batches["wire-batch-2"]["is_revert"] is False
