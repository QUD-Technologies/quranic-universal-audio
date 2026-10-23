"""``restore_fatiha_basmala`` — undo the deletion of Al-Fatiha's 1:1."""

from __future__ import annotations

from restore_fatiha_basmala import _restore, has_verse_1, latest_deletion, relabel_special

VERSE_1 = {"matched_ref": "1:1:1-1:1:4", "time_start": 300, "time_end": 5200, "segment_uid": "u1"}


def _batch(batch_id: str, *ops: dict, **extra) -> dict:
    return {"batch_id": batch_id, "saved_at_utc": f"t{batch_id}", "operations": list(ops), **extra}


def _delete(op_id: str, snap: dict = VERSE_1) -> dict:
    return {"op_id": op_id, "targets_before": [snap], "targets_after": []}


def test_chapter_1_without_its_verse_1_needs_a_restore():
    ch1 = [{"ref": "1", "segments": [{"matched_ref": "1:2:1-1:2:4"}]}]
    assert not has_verse_1(ch1)
    assert has_verse_1([{"ref": "1", "segments": [VERSE_1]}])


def test_the_latest_unreverted_deletion_is_picked():
    history = [
        _batch("a", _delete("o1")),
        _batch("b", _delete("o2")),
        _batch("c", reverts_batch_id="b", reverts_op_ids=["o2"]),  # o2 already undone
        _batch("d", _delete("o3", {"matched_ref": "1:2:1-1:2:4", "time_start": 1, "time_end": 2})),
    ]
    found = latest_deletion(history)
    assert found is not None and (found.batch_id, found.op_id) == ("a", "o1")
    assert (found.time_start, found.time_end) == (300, 5200)


def test_a_fully_reverted_batch_is_skipped():
    history = [_batch("a", _delete("o1")), _batch("r", reverts_batch_id="a")]
    assert latest_deletion(history) is None


def test_the_special_becomes_the_restored_verse_and_keeps_its_voice():
    doc = {"chapters": {"1": [{
        "type": "Basmala", "position": "pre", "start_ms": 280, "end_ms": 5100, "source": "user_delete",
        "clipped": True, "voice_verdict": "different", "voice_confirmed": False,
    }]}}  # fmt: skip
    assert relabel_special(doc, 300, 5200)
    entry = doc["chapters"]["1"][0]
    assert (entry["source"], entry["start_ms"], entry["end_ms"], entry["clipped"]) == (
        "fatiha_verse", 300, 5200, False,
    )  # fmt: skip
    assert (entry["voice_verdict"], entry["voice_confirmed"]) == ("different", False)


def test_a_dry_run_writes_nothing():
    calls: list = []
    deps = {
        "load_detailed": lambda slug: [{"ref": "1", "segments": []}],
        "parse_history": lambda slug: [_batch("a", _delete("o1"))],
        "undo_ops": lambda *a, **k: calls.append(a),
    }
    assert _restore("r", False, deps) == "would_restore"
    assert calls == []
