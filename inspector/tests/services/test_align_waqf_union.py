"""``waqf_union``: one Low Confidence Waqf card per segment from the neural and matcher arms."""

from services.admin.align_pipeline import waqf_union


def _cut(axis, after, nxt, cursor, score=1000):
    return {
        "cursor_ms": cursor, "axes": [axis], "gap_ms": 100, "score": score, "word": "w",
        "verse_end": False, "evidence": {axis: {"after_ref": after, "next_ref": nxt}},
    }  # fmt: skip


def _item(cuts, refs):
    return {"kind": "missed_waqf", "chapter": 2, "cursors": [c["cursor_ms"] for c in cuts],
            "refs": refs, "score": max(c["score"] for c in cuts), "cuts": cuts}  # fmt: skip


def test_a_join_both_arms_flag_is_one_cut_with_both_axes():
    neural = _item([_cut("neural", "2:5:3", "2:5:4", 4000, 2000)], ["2:5:1-2:5:3", "2:5:4-2:5:9"])
    lattice = _item([_cut("lattice", "2:5:3", "2:5:4", 3900)], ["2:5:1-2:5:3", "2:5:4-2:5:9"])

    item = waqf_union.merge_item(neural, lattice)

    assert item is not None
    assert item["cursors"] == [4000], "the neural cursor"
    assert item["cuts"][0]["axes"] == ["neural", "lattice"]
    assert set(item["cuts"][0]["evidence"]) == {"neural", "lattice"}
    assert item["score"] == 2000


def test_different_joins_of_one_segment_make_one_card_with_both_cuts():
    neural = _item([_cut("neural", "2:5:6", "2:5:7", 7000)], ["2:5:1-2:5:6", "2:5:7-2:5:9"])
    lattice = _item([_cut("lattice", "2:5:3", "2:5:4", 4000)], ["2:5:1-2:5:3", "2:5:4-2:5:9"])

    item = waqf_union.merge_item(neural, lattice)

    assert item is not None
    assert item["cursors"] == [4000, 7000]
    assert item["refs"] == ["2:5:1-2:5:3", "2:5:4-2:5:6", "2:5:7-2:5:9"]


def test_a_matcher_pause_at_a_sakt_boundary_is_not_asked():
    lattice = _item(
        [_cut("lattice", "75:27:2", "75:27:3", 2000)], ["75:27:1-75:27:2", "75:27:3-75:27:3"]
    )

    assert waqf_union.merge_item(None, lattice) is None


def test_cuts_that_do_not_tile_the_segment_fall_back_to_the_neural_item():
    neural = _item([_cut("neural", "2:5:6", "2:5:7", 3000)], ["2:5:1-2:5:6", "2:5:7-2:5:9"])
    lattice = _item([_cut("lattice", "2:5:3", "2:5:4", 5000)], ["2:5:1-2:5:3", "2:5:4-2:5:9"])

    assert waqf_union.merge_item(neural, lattice) is neural


def test_merge_counts_each_arm():
    neural = {"_meta": {"method": "neural_checks", "arms": {"neural": {}}}, "by_uid": {
        "a": _item([_cut("neural", "2:5:3", "2:5:4", 4000)], ["2:5:1-2:5:3", "2:5:4-2:5:9"]),
    }}  # fmt: skip
    lattice = {"_meta": {"arms": {"lattice": {}}}, "by_uid": {
        "a": _item([_cut("lattice", "2:5:3", "2:5:4", 3900)], ["2:5:1-2:5:3", "2:5:4-2:5:9"]),
        "b": _item([_cut("lattice", "2:6:2", "2:6:3", 9000)], ["2:6:1-2:6:2", "2:6:3-2:6:5"]),
    }}  # fmt: skip

    doc = waqf_union.merge(neural, lattice)

    assert list(doc["by_uid"]) == ["a", "b"]
    assert doc["_meta"]["by_axes"] == {"neural": 1, "lattice": 2, "both": 1}
    assert set(doc["_meta"]["arms"]) == {"neural", "lattice"}
    assert doc["_meta"]["segments"] == 2
