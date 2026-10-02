"""Mark-ready checkboxes apply only when one of their categories had an item."""

from services.validation.checklist_scope import applicable_checklist_keys

CUTOFF = 0.80
NO_HISTORY = {"batches": []}


def _seg(uid, ref="2:1:1-2:1:3", ignored=None):
    return {"segment_uid": uid, "matched_ref": ref, "ignored_categories": ignored or []}


def _entries(*segs):
    return [{"ref": "2", "segments": list(segs)}]


def test_a_clean_reciter_needs_no_box():
    assert applicable_checklist_keys({}, _entries(_seg("a")), NO_HISTORY, CUTOFF) == []


def test_live_items_open_their_boxes_in_checklist_order():
    result = {
        "missed_waqf": [{"segment_uid": "a", "resolved": True}],
        "failed": [{"segment_uid": "b"}],
    }
    keys = applicable_checklist_keys(result, _entries(_seg("a")), NO_HISTORY, CUTOFF)
    assert keys == ["failed_alignments", "splits_wasl_waqf"]


def test_low_confidence_counts_only_items_under_the_strict_cutoff():
    above = {"low_confidence": [{"segment_uid": "a", "confidence": 0.95}]}
    below = {"low_confidence": [{"segment_uid": "a", "confidence": 0.5}]}
    assert applicable_checklist_keys(above, _entries(), NO_HISTORY, CUTOFF) == []
    assert applicable_checklist_keys(below, _entries(), NO_HISTORY, CUTOFF) == ["low_confidence"]


def test_an_ignored_item_keeps_its_box():
    entries = _entries(_seg("a", ignored=["repetitions"]))
    assert applicable_checklist_keys({}, entries, NO_HISTORY, CUTOFF) == ["repetitions"]


def test_an_item_edited_away_keeps_its_box():
    history = {
        "batches": [
            {"operations": [{"op_context_category": "basmala_amin", "targets_before": []}]},
            {"operations": [{"targets_before": [{"segment_uid": "f", "matched_ref": ""}]}]},
        ]
    }
    keys = applicable_checklist_keys({}, _entries(), history, CUTOFF)
    assert keys == ["failed_alignments", "basmala_amin_intros"]
