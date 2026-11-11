"""``replay_lost_edits`` — edits logged in history but missing from detailed.json."""

from __future__ import annotations

from replay_lost_edits import apply_restore, lost_edits

VERDICT = {"at_ms": 500, "after_ref": "1:2:2", "verdict": "wasl"}


def _seg(uid: str, **extra) -> dict:
    return {
        "segment_uid": uid,
        "time_start": 100,
        "time_end": 900,
        "matched_ref": "1:2:1-1:2:4",
        "confidence": 0.9,
        **extra,
    }


def _ignore_batch(batch_id: str, at: str, uid: str, **after) -> dict:
    return {
        "batch_id": batch_id,
        "saved_at_utc": at,
        "operations": [
            {
                "op_id": f"{batch_id}-op",
                "op_type": "ignore_issue",
                "targets_before": [{**_seg(uid), "chapter": 1}],
                "targets_after": [{**_seg(uid, **after), "chapter": 1}],
            }
        ],
    }


IGNORED = {"ignored_categories": ["missed_waqf"], "join_verdicts": [VERDICT], "confidence": 1}


def test_restores_the_fields_the_op_set():
    live = {"a": _seg("a", ignored_categories=[])}
    history = [_ignore_batch("b1", "2026-10-10T15:15:20Z", "a", **IGNORED)]
    [restore] = lost_edits(history, live)
    assert restore.fields == IGNORED
    apply_restore(live["a"], restore.fields)
    assert lost_edits(history, live) == []


def test_skips_segments_already_carrying_the_edit_or_moved_since():
    history = [
        _ignore_batch("b1", "2026-10-10T15:15:20Z", "kept", **IGNORED),
        _ignore_batch("b2", "2026-10-10T15:15:21Z", "moved", **IGNORED),
        _ignore_batch("b3", "2026-10-10T15:15:22Z", "gone", **IGNORED),
    ]
    live = {"kept": _seg("kept", **IGNORED), "moved": _seg("moved", time_end=950)}
    assert lost_edits(history, live) == []


def test_only_the_latest_unreverted_op_counts():
    later_clear = _ignore_batch("b2", "2026-10-10T15:16:00Z", "a")
    later_clear["operations"][0]["targets_before"] = [{**_seg("a", **IGNORED), "chapter": 1}]
    history = [_ignore_batch("b1", "2026-10-10T15:15:20Z", "a", **IGNORED), later_clear]
    assert lost_edits(history, {"a": _seg("a")}) == []

    reverted = [
        _ignore_batch("b1", "2026-10-10T15:15:20Z", "a", **IGNORED),
        {"batch_id": "r1", "saved_at_utc": "2026-10-10T15:17:00Z", "reverts_batch_id": "b1"},
    ]
    assert lost_edits(reverted, {"a": _seg("a")}) == []


def test_cleared_list_fields_are_dropped():
    seg = _seg("a", **IGNORED)
    apply_restore(seg, {"ignored_categories": [], "join_verdicts": []})
    assert "ignored_categories" not in seg and "join_verdicts" not in seg
