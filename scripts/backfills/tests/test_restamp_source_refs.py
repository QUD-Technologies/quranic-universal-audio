"""``restamp_source_refs`` — only the chapters whose provenance moved need a TS regen."""

from __future__ import annotations

from restamp_source_refs import restamp

_FRESH = {"7:136:12-7:137:2": "7:137:12-7:137:20"}


def _stamp(seg: dict) -> None:
    seg["source_ref"] = _FRESH.get(seg["matched_ref"], seg.get("source_ref"))
    seg["projection_support"] = "full"


def _entries() -> list[dict]:
    return [
        {
            "ref": "6",
            "segments": [
                {
                    "matched_ref": "6:1:1-6:1:3",
                    "source_ref": "6:1:1-6:1:3",
                    "projection_support": "full",
                }
            ],
        },
        {
            "ref": "7",
            "segments": [
                {
                    "matched_ref": "7:1:1-7:1:2",
                    "source_ref": "7:1:1-7:1:2",
                    "projection_support": "full",
                },
                {
                    "matched_ref": "7:136:12-7:137:2",
                    "source_ref": "7:137:12-7:137:18",
                    "projection_support": "full",
                },
            ],
        },
    ]


def test_a_truncated_cross_verse_stamp_is_the_only_chapter_reported():
    entries = _entries()
    assert restamp(entries, _stamp) == {7: 1}
    assert entries[1]["segments"][1]["source_ref"] == "7:137:12-7:137:20"


def test_a_support_change_alone_counts_as_moved():
    entries = _entries()
    entries[0]["segments"][0]["projection_support"] = "partial"
    assert restamp(entries, _stamp) == {6: 1, 7: 1}
