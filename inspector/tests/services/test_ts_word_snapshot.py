"""Report targets inside a word-profile shard.

Every report on a non-Hafs delivery used to 409 with "native report target does
not resolve" — including the `audio` and `other` comment flows, which the FE
deliberately still offers there (only `tajweed` and `phonemes` are `nativeOnly`).
"""

from __future__ import annotations

import pytest

from services.ts_reports import ts_target_snapshot
from services.ts_reports.ts_word_snapshot import resolve_word_target

# One reading of Qalun 112:1 (4 words) waṣl into 112:2 (2 words), stopping at
# the end of verse 2. Boundary states are stored per word: `1` = join, `3` = stop.
WORD_DOC = {
    "_meta": {
        "schema_version": 14,
        "profile": "word",
        "chapter": 112,
        "audio_category": "by_surah",
        "riwayah": "qalun",
        "edition_id": "qalun-v21",
        "words_sha256": "a" * 64,
        "timing_provider": "hafs_proxy_mfa",
        "reference_riwayah": "hafs",
        "reference_id": "ref",
    },
    "readings": [
        {
            "id": "r1",
            "parts": [["112:1", 0, 2900, 0, 4], ["112:2", 2900, 4200, 4, 2]],
            "words": [
                ["112:1:1", "A", 0, 700],
                ["112:1:2", "B", 800, 1500],
                ["112:1:3", "C", 1500, 2200],
                ["112:1:4", "D", 2200, 2900],
                ["112:2:1", "E", 3000, 3600],
                ["112:2:2", "F", 3700, 4200],
            ],
            "boundaries": [
                [1, None],
                [1, None],
                [1, None],
                [1, 1],
                [1, None],
                [3, 2],
            ],
        }
    ],
}

NATIVE_META = {"schema_version": 15, "chapter": 112, "audio_category": "by_surah"}


def _target(kind: str, target_id) -> dict:
    return {"reading_id": "r1", "kind": kind, "target_id": str(target_id)}


def test_the_verse_target_the_comment_composers_send_resolves():
    # ReportComposer posts {reading_id, kind:'verse', target_id: verseKey} for
    # both `audio` and `other`. This is the path that 409'd.
    snap = resolve_word_target(WORD_DOC, "112:1", _target("verse", "112:1"))

    assert snap is not None
    assert snap["shard_profile"] == "word"
    assert snap["native_schema_version"] is None
    assert snap["shard_schema_version"] == 14
    assert snap["timing"] == {"start_ms": 0, "end_ms": 2900}


def test_a_word_target_carries_the_editions_own_text_as_its_identity():
    # A re-stamp that moved the projection would put a different word at this
    # index; the report has to read as stale, not silently re-point.
    snap = resolve_word_target(WORD_DOC, "112:1", _target("word", 2))

    assert snap is not None
    assert snap["native"] == {"word_id": 2, "ref": "112:1:3", "text": "C"}
    assert snap["timing"] == {"start_ms": 1500, "end_ms": 2200}


def test_a_boundary_target_is_the_gap_before_its_word():
    # Ids run 0..len(words), matching `boundariesOf` in the FE decoder: id 0 is
    # the lead-in, id i the gap between word i-1 and word i.
    snap = resolve_word_target(WORD_DOC, "112:1", _target("boundary", 1))

    assert snap is not None
    assert snap["timing"] == {"start_ms": 700, "end_ms": 800}
    assert snap["native"]["before"] == "112:1:1"
    assert snap["native"]["after"] == "112:1:2"


def test_the_trailing_boundary_of_the_last_word_resolves():
    snap = resolve_word_target(WORD_DOC, "112:2", _target("boundary", 6))

    assert snap is not None
    native = snap["native"]
    assert isinstance(native, dict)
    assert native["after"] is None
    assert native["verse_end"] == 2


def test_a_target_in_another_verse_is_refused():
    # The verse key and the target must agree, or a report filed on one verse
    # would carry another verse's fingerprint.
    assert resolve_word_target(WORD_DOC, "112:2", _target("word", 0)) is None
    assert resolve_word_target(WORD_DOC, "112:1", _target("verse", "112:2")) is None


@pytest.mark.parametrize("kind", ["tajweed", "sound", "column", "group", "bridge"])
def test_kinds_a_word_profile_has_no_facts_for_are_refused(kind):
    # These are hidden in the FE (`nativeOnly`); resolving them would let a
    # reader file a report against nothing.
    assert resolve_word_target(WORD_DOC, "112:1", _target(kind, "0")) is None


def test_an_unknown_reading_is_refused():
    assert (
        resolve_word_target(
            WORD_DOC, "112:1", {"reading_id": "r9", "kind": "verse", "target_id": "112:1"}
        )
        is None
    )


def test_the_shared_entry_point_routes_by_profile():
    # `resolve_target` is what the routes call; it must not need to know which
    # profile it was handed.
    snap = ts_target_snapshot.resolve_target(WORD_DOC, "112:1", _target("verse", "112:1"))

    assert snap is not None and snap["shard_profile"] == "word"


def test_a_native_snapshot_still_says_native():
    doc = {"_meta": NATIVE_META, "readings": []}
    assert ts_target_snapshot.resolve_target(doc, "112:1", _target("verse", "112:1")) is None
