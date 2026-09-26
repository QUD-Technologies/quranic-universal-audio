"""Resolved-by-edit suppression tests.

Edits dispatched from a validation accordion card record
``op_context_category`` on the op. The validate route consults
edit_history.jsonl, builds a {uid: {category, ...}} index, injects
``_resolved_by_edit`` onto each segment, and the classifier suppresses
the listed categories without writing to ``ignored_categories``.

Scope: ``boundary_adj``, ``audio_bleeding``, ``repetitions``,
``basmala_amin``, ``low_confidence_v2``. ``cross_verse`` and
chapter/verse-level categories are excluded -- they stay until the validator
clears them. ``qalqala`` is view-only (mirrors ``muqattaat``) and is also
excluded so the flag stays after edits. ``low_confidence`` (v1) is excluded
because it self-resolves via the edit-time ``confidence = 1.0`` bump; v2 is
included because it's keyed against a frozen extraction-time sidecar that
``confidence`` doesn't affect.
"""

from __future__ import annotations

import json

import pytest

from services.history_query import (
    RESOLVES_BY_EDIT_CATEGORIES,
    build_resolved_by_edit_index,
)
from services.validation.classifier import (
    classify_flags,
    is_resolved_by_edit,
    is_suppressed_for,
)

# ---------------------------------------------------------------------------
# Unit: classifier helpers
# ---------------------------------------------------------------------------


def test_is_resolved_by_edit_reads_seg_field():
    seg = {"_resolved_by_edit": {"boundary_adj"}}
    assert is_resolved_by_edit(seg, "boundary_adj") is True
    assert is_resolved_by_edit(seg, "audio_bleeding") is False


def test_is_resolved_by_edit_no_field():
    assert is_resolved_by_edit({}, "boundary_adj") is False
    assert is_resolved_by_edit({"_resolved_by_edit": set()}, "boundary_adj") is False


def test_classify_drops_low_confidence_v2_when_resolved_by_edit():
    """Even when ``segment_uid`` is in ``probe_failed_uids``, an edit
    dispatched from the v2 card (yielding ``_resolved_by_edit`` membership)
    suppresses the flag."""
    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 1.0,
        "segment_uid": "uid-V",
        "_resolved_by_edit": {"low_confidence_v2"},
    }
    flags = classify_flags(
        seg,
        entry_ref="112",
        is_by_ayah=False,
        surah=112,
        s_ayah=1,
        e_ayah=1,
        s_word=1,
        e_word=4,
        single_word_verses=set(),
        canonical=None,
        probe_failed_uids={"uid-V"},
    )
    assert flags["low_confidence_v2"] is False


def test_classify_keeps_low_confidence_v2_without_resolved_by_edit():
    """Negative control: same probe-failed uid, no resolved-by-edit → flag stays."""
    seg = {
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 1.0,
        "segment_uid": "uid-V",
    }
    flags = classify_flags(
        seg,
        entry_ref="112",
        is_by_ayah=False,
        surah=112,
        s_ayah=1,
        e_ayah=1,
        s_word=1,
        e_word=4,
        single_word_verses=set(),
        canonical=None,
        probe_failed_uids={"uid-V"},
    )
    assert flags["low_confidence_v2"] is True


def test_is_suppressed_for_combines_ignored_and_resolved():
    seg_ignored = {"ignored_categories": ["boundary_adj"]}
    assert is_suppressed_for(seg_ignored, "boundary_adj") is True

    seg_resolved = {"_resolved_by_edit": {"audio_bleeding"}}
    assert is_suppressed_for(seg_resolved, "audio_bleeding") is True

    seg_neither = {}
    assert is_suppressed_for(seg_neither, "boundary_adj") is False


def test_basmala_amin_resolved_by_edit_suppresses():
    seg = {"_resolved_by_edit": {"basmala_amin"}}
    assert is_resolved_by_edit(seg, "basmala_amin") is True
    assert is_suppressed_for(seg, "basmala_amin") is True
    # An ignored basmala still suppresses via the ignore branch.
    seg_ignored = {"ignored_categories": ["basmala_amin"]}
    assert is_suppressed_for(seg_ignored, "basmala_amin") is True


def test_resolves_by_edit_set_contains_only_soft_categories():
    """The set must match the user's pick: boundary_adj / audio_bleeding /
    repetitions / basmala_amin / low_confidence_v2.

    ``qalqala`` is intentionally excluded — it's view-only (like ``muqattaat``)
    so editing a qalqala-flagged seg leaves the flag in place for the next
    validation pass; the edit history still carries the ``qalqala`` pill via
    ``op_context_category``. ``low_confidence`` (v1) is excluded because it
    self-resolves via the confidence=1.0 bump; ``low_confidence_v2`` is included
    because it's keyed against a frozen probe sidecar that confidence doesn't
    affect. ``basmala_amin`` is included because any edit from the card signals
    "I dealt with it" — revalidation must not re-raise the flag for that uid.
    ``missed_waqf`` is included so a split from its card resolves the root.
    """
    assert RESOLVES_BY_EDIT_CATEGORIES == frozenset(
        {
            "boundary_adj",
            "audio_bleeding",
            "repetitions",
            "low_confidence_v2",
            "basmala_amin",
            "missed_waqf",
        }
    )


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------


def _write_history(tmp_path, reciter, records):
    rec_dir = tmp_path / reciter
    rec_dir.mkdir(parents=True, exist_ok=True)
    p = rec_dir / "edit_history.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )
    return p


def test_build_index_from_single_op(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "trim_segment",
                        "op_context_category": "boundary_adj",
                        "targets_after": [{"segment_uid": "uid-A"}],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {"uid-A": {"boundary_adj"}}


def test_build_index_skips_excluded_categories(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "split_segment",
                        "op_context_category": "cross_verse",
                        "targets_after": [{"segment_uid": "uid-A"}],
                    },
                    {
                        "op_id": "o2",
                        "op_type": "trim_segment",
                        "op_context_category": "low_confidence",
                        "targets_after": [{"segment_uid": "uid-B"}],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    # cross_verse is hard; low_confidence self-resolves via confidence=1.0.
    assert idx == {}


def test_build_index_accumulates_categories_per_uid(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "trim_segment",
                        "op_context_category": "boundary_adj",
                        "targets_after": [{"segment_uid": "uid-A"}],
                    },
                    {
                        "op_id": "o2",
                        "op_type": "edit_reference",
                        "op_context_category": "audio_bleeding",
                        "targets_after": [{"segment_uid": "uid-A"}],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {"uid-A": {"boundary_adj", "audio_bleeding"}}


def test_build_index_includes_basmala_amin(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "trim_segment",
                        "op_context_category": "basmala_amin",
                        "targets_after": [{"segment_uid": "uid-bsm"}],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {"uid-bsm": {"basmala_amin"}}


def test_build_index_split_marks_both_halves(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "split_segment",
                        "op_context_category": "repetitions",
                        "targets_after": [
                            {"segment_uid": "uid-first"},
                            {"segment_uid": "uid-second"},
                        ],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {
        "uid-first": {"repetitions"},
        "uid-second": {"repetitions"},
    }


def test_build_index_picks_up_low_confidence_v2(monkeypatch, tmp_path):
    """v2 differs from v1: confidence=1.0 doesn't clear it (the v2 signal is
    sidecar-keyed on segment_uid, not on confidence), so the index MUST carry
    ``low_confidence_v2`` ops so the validator can suppress the flag.
    """
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "trim_segment",
                        "op_context_category": "low_confidence_v2",
                        "targets_after": [{"segment_uid": "uid-V"}],
                    },
                ],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {"uid-V": {"low_confidence_v2"}}


def test_build_index_skips_reverted_batches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    _write_history(
        tmp_path,
        "r1",
        [
            {"record_type": "genesis", "batch_id": "g", "operations": []},
            {
                "batch_id": "b1",
                "operations": [
                    {
                        "op_id": "o1",
                        "op_type": "trim_segment",
                        "op_context_category": "audio_bleeding",
                        "targets_after": [{"segment_uid": "uid-X"}],
                    },
                ],
            },
            {
                "batch_id": "b2",
                "reverts_batch_id": "b1",
                "operations": [],
            },
        ],
    )
    idx = build_resolved_by_edit_index("r1")
    assert idx == {}


def test_build_index_no_history_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "services.history_query.RECITATION_SEGMENTS_PATH",
        tmp_path,
    )
    assert build_resolved_by_edit_index("nonexistent") == {}


# ---------------------------------------------------------------------------
# Integration: validate route honors resolved-by-edit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", sorted(RESOLVES_BY_EDIT_CATEGORIES))
def test_validate_drops_resolved_category(
    category, flask_client, tmp_reciter_dir, load_fixture, monkeypatch
):
    """End-to-end: a resolved-by-edit entry in history makes the validator
    drop that category for that segment_uid in the response."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    fixture = load_fixture("112-ikhlas")
    target_uid = fixture["entries"][0]["segments"][0]["segment_uid"]

    # Append a synthetic resolving op to history.
    history_path = tmp_reciter_dir.root / reciter / "edit_history.jsonl"
    record = {
        "batch_id": "synthetic-resolve",
        "operations": [
            {
                "op_id": "synthetic-op",
                "op_type": "trim_segment",
                "op_context_category": category,
                "targets_after": [{"segment_uid": target_uid}],
            },
        ],
    }
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    # Drop any cached resolved index so the new history is read.
    from services import cache as _cache

    _cache.invalidate_seg_caches(reciter)

    resp = flask_client.get(f"/api/seg/validate/{reciter}").get_json()
    assert resp is not None

    # The category's detail list must not contain target_uid.
    items = resp.get(category, []) or []
    for it in items:
        assert it.get("segment_uid") != target_uid, (
            f"validate response still flags {target_uid} for {category} "
            f"despite resolved-by-edit history record"
        )
