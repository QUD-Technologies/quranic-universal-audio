"""``clamp_opener_spans`` — an opener ends where the next sound starts."""

from __future__ import annotations

from clamp_opener_spans import clamp_chapter, first_verse_starts


def _opener(kind: str, start: int, end: int, source: str = "aligner_backfill") -> dict:
    return {
        "type": kind,
        "position": "pre",
        "start_ms": start,
        "end_ms": end,
        "source": source,
        "clipped": False,
        "words": [[start + 10, end - 10]],
    }


def test_a_basmala_running_into_ayah_1_ends_at_its_start_and_loses_its_words():
    items = [_opener("Basmala", 680, 3847)]
    clamp_chapter(items, 3790)
    assert (items[0]["end_ms"], items[0]["clipped"], items[0]["words"]) == (3790, True, [])


def test_an_istiadha_running_into_the_basmala_ends_at_the_basmala():
    items = [_opener("Isti'adha", 480, 6713), _opener("Basmala", 6690, 12197)]
    clamp_chapter(items, 12500)
    assert [(e["end_ms"], e["clipped"]) for e in items] == [(6690, True), (12197, False)]


def test_al_fatihas_own_basmala_verse_is_never_clamped():
    items = [_opener("Basmala", 8760, 14425, source="fatiha_verse")]
    assert clamp_chapter(items, 8760) == []
    assert items[0]["end_ms"] == 14425


def test_first_verse_start_skips_labelled_special_rows():
    entries = [
        {
            "ref": "2",
            "segments": [
                {"matched_ref": "Basmala", "time_start": 100},
                {"matched_ref": "2:1:1-2:1:1", "time_start": 3790},
                {"matched_ref": "2:2:1-2:2:5", "time_start": 9000},
            ],
        }
    ]
    assert first_verse_starts(entries, frozenset({"Basmala"})) == {2: 3790}
