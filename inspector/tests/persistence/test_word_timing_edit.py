"""Sample word edits validate bounds, stale input, and a reversible history patch."""

from services.segments.save import _apply_word_timing_op


def _fixture():
    segment = {
        "segment_uid": "uid-1", "time_start": 100, "time_end": 900,
        "matched_ref": "112:1:1-112:1:2", "confidence": 0.9,
        "word_timings": [
            {"word": "one", "location": "112:1:1", "start_ms": 120, "end_ms": 400},
            {"word": "two", "location": "112:1:2", "start_ms": 420, "end_ms": 880},
        ],
    }
    matching = [{"ref": "112:1:1-112:1:2", "segments": [segment]}]
    op = {
        "type": "editWordTimings", "op_type": "edit_word_timings",
        "command": {
            "type": "editWordTimings", "segmentUid": "uid-1",
            "expected": [{"start_ms": 120, "end_ms": 400}, {"start_ms": 420, "end_ms": 880}],
            "boundaries": [{"start_ms": 120, "end_ms": 410}, {"start_ms": 430, "end_ms": 880}],
        },
    }
    return matching, {"segments": [], "operations": [op]}, segment, op


def test_sample_edit_builds_reversible_patch():
    matching, updates, segment, op = _fixture()
    assert _apply_word_timing_op(matching, updates, reciter="sample--abc", chapter=112) is None
    assert segment["word_timings"][0]["end_ms"] == 410
    assert op["patch"]["before"][0]["word_timings"][0]["end_ms"] == 400
    assert op["patch"]["after"][0]["word_timings"][0]["end_ms"] == 410


def test_word_edit_rejects_stale_or_invalid_or_non_sample():
    matching, updates, segment, _ = _fixture()
    assert _apply_word_timing_op(matching, updates, reciter="reciter", chapter=112)[1] == 400
    updates["operations"][0]["command"]["expected"][0]["end_ms"] = 401
    assert _apply_word_timing_op(matching, updates, reciter="sample--abc", chapter=112)[1] == 409
    updates["operations"][0]["command"]["expected"][0]["end_ms"] = 400
    updates["operations"][0]["command"]["boundaries"][1]["start_ms"] = 400
    assert _apply_word_timing_op(matching, updates, reciter="sample--abc", chapter=112)[1] == 400
    assert segment["word_timings"][0]["end_ms"] == 400
