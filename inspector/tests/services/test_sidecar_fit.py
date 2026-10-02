"""Boundary sidecar entries are dropped once their segment changes under them."""

from services.validation.sidecar_fit import entry_fits_segment, fitting_entries

# A 34:1 row the align pipeline first matched as Al-Fatiha 1:2-1:4; its
# missed-waqf entry was built against those refs before the ref was corrected.
SEG = {
    "segment_uid": "u1",
    "time_start": 10400,
    "time_end": 18600,
    "matched_ref": "34:1:1-34:1:10",
}
FATIHA_ENTRY = {
    "kind": "missed_waqf",
    "cursors": [14700, 16820],
    "refs": ["1:2:1-1:2:4", "1:3:1-1:3:2", "1:4:1-1:4:2"],
}
LIVE_ENTRY = {
    "kind": "missed_waqf",
    "cursors": [14700],
    "refs": ["34:1:1-34:1:5", "34:1:6-34:1:10"],
}


def test_entry_built_for_an_old_ref_does_not_fit():
    assert not entry_fits_segment(FATIHA_ENTRY, SEG)


def test_entry_spanning_the_current_ref_fits():
    assert entry_fits_segment(LIVE_ENTRY, SEG)


def test_entry_with_a_cursor_outside_a_trimmed_segment_does_not_fit():
    trimmed = {**SEG, "time_end": 14000}
    assert not entry_fits_segment(LIVE_ENTRY, trimmed)


def test_entry_without_refs_is_judged_on_cursors():
    assert entry_fits_segment({"cursors": [12000]}, SEG)
    assert not entry_fits_segment({"cursors": [9000]}, SEG)


def test_fitting_entries_keeps_live_and_drops_stale_and_orphaned():
    other = {**SEG, "segment_uid": "u2", "matched_ref": "34:2:1-34:2:5"}
    by_uid = {"u1": LIVE_ENTRY, "u2": FATIHA_ENTRY, "gone": LIVE_ENTRY}
    entries = [{"ref": "34", "segments": [SEG, other]}]
    assert fitting_entries(by_uid, entries) == {"u1": LIVE_ENTRY}
