"""A special a reviewer labels in detailed.json (Edit Ref → ``Basmala`` …) is mirrored into specials.json."""

from __future__ import annotations

import json

import pytest

from qua_shared.schemas.bucket.specials import SpecialsDoc
from services.segments import save, specials
from services.timing.specials_timing import with_openers

RECITER = "fixture_reciter"
_ALIGNED = {
    "schema_version": 1,
    "generated_at": "t",
    "chapters": {
        "2": [
            {"type": "Isti'adha", "start_ms": 100, "end_ms": 3000, "source": "align"},
            {"type": "Basmala", "start_ms": 3100, "end_ms": 6000, "source": "align"},
        ]
    },
}


@pytest.fixture
def specials_path(tmp_path, tmp_reciter_dir, monkeypatch):
    tmp_reciter_dir.install(RECITER, "112-ikhlas")
    monkeypatch.setattr(save.retime_queue, "schedule", lambda *_a, **_k: None)
    path = tmp_path / "reciters" / RECITER / "specials.json"
    path.write_text(json.dumps(_ALIGNED), "utf-8")
    return path


def _row(ref: str, start: int, end: int) -> dict:
    return {"matched_ref": ref, "time_start": start, "time_end": end, "confidence": 1.0}


def _persist(*rows: dict, ref: str = "2") -> None:
    save.persist_detailed(RECITER, {}, [{"ref": ref, "segments": list(rows)}], [2])


def _chapter_2(path) -> list[tuple]:
    doc = SpecialsDoc.model_validate_json(path.read_text("utf-8"))
    return [(e.type, e.position, e.start_ms, e.end_ms, e.source) for e in doc.chapters.get("2", [])]


def test_a_labelled_basmala_replaces_the_recorded_one(specials_path):
    _persist(_row("Basmala", 3200, 6400), _row("2:1:1-2:1:1", 6500, 9000))
    assert _chapter_2(specials_path) == [
        ("Isti'adha", "pre", 100, 3000, "align"),
        ("Basmala", "pre", 3200, 6400, "in_detailed"),
    ]


def test_relabelling_the_row_as_a_verse_drops_its_entry(specials_path):
    _persist(_row("Basmala", 3200, 6400), _row("2:1:1-2:1:1", 6500, 9000))
    _persist(_row("2:1:1-2:1:1", 3200, 9000))
    assert _chapter_2(specials_path) == [("Isti'adha", "pre", 100, 3000, "align")]


def test_a_special_inside_the_chapter_is_not_recorded(specials_path):
    _persist(
        _row("2:1:1-2:1:1", 100, 3000), _row("Takbir", 3100, 4000), _row("2:2:1-2:2:5", 4100, 9000)
    )
    assert _chapter_2(specials_path) == [
        ("Isti'adha", "pre", 100, 3000, "align"),
        ("Basmala", "pre", 3100, 6000, "align"),
    ]


def test_by_ayah_audio_is_left_alone(specials_path):
    _persist(_row("Basmala", 0, 2000), _row("2:1:1-2:1:1", 2100, 4000), ref="2:1")
    assert _chapter_2(specials_path) == [
        ("Isti'adha", "pre", 100, 3000, "align"),
        ("Basmala", "pre", 3100, 6000, "align"),
    ]


def test_timing_sends_the_opener_not_the_labelled_row():
    entries = [
        {"ref": "2", "segments": [_row("Basmala", 3200, 6400), _row("2:1:1-2:1:1", 6500, 9000)]}
    ]
    opener = {**_row("Basmala", 3200, 6400), "segment_uid": "o"}
    [entry] = with_openers(entries, [opener])
    assert [s.get("segment_uid") for s in entry["segments"]] == ["o", None]
    assert [s["matched_ref"] for s in with_openers(entries, [])[0]["segments"]] == ["2:1:1-2:1:1"]


def _persist_fatiha(*rows: dict) -> None:
    save.persist_detailed(RECITER, {}, [{"ref": "1", "segments": list(rows)}], [1])


def _chapter_1(path) -> list[tuple]:
    doc = SpecialsDoc.model_validate_json(path.read_text("utf-8"))
    return [
        (e.type, e.start_ms, e.end_ms, e.source, e.voice_verdict) for e in doc.chapters.get("1", [])
    ]


def test_a_1_1_split_from_1_2_gets_its_fatiha_basmala_entry(specials_path):
    # An align run whose Basmala only came apart from 1:2 at the verse-end split.
    _persist_fatiha(_row("1:1:1-1:1:4", 120, 3240), _row("1:2:1-1:2:4", 3240, 6905))
    assert _chapter_1(specials_path) == [("Basmala", 120, 3240, "fatiha_verse", None)]


def test_a_moved_1_1_row_moves_its_entry_and_keeps_the_voice_call(specials_path):
    doc = json.loads(specials_path.read_text("utf-8"))
    doc["chapters"]["1"] = [
        {"type": "Basmala", "start_ms": 100, "end_ms": 3000, "source": "fatiha_verse",
         "voice_verdict": "same", "voice_z": 0.9}
    ]  # fmt: skip
    specials_path.write_text(json.dumps(doc), "utf-8")
    _persist_fatiha(_row("1:1:1-1:1:4", 150, 3300), _row("1:2:1-1:2:4", 3400, 6905))
    assert _chapter_1(specials_path) == [("Basmala", 150, 3300, "fatiha_verse", "same")]


def test_a_deleted_1_1_keeps_its_entry_for_the_restore(specials_path):
    doc = json.loads(specials_path.read_text("utf-8"))
    doc["chapters"]["1"] = [
        {"type": "Basmala", "start_ms": 100, "end_ms": 3000, "source": "fatiha_verse"}
    ]
    specials_path.write_text(json.dumps(doc), "utf-8")
    _persist_fatiha(_row("1:2:1-1:2:4", 3400, 6905))
    assert _chapter_1(specials_path) == [("Basmala", 100, 3000, "fatiha_verse", None)]


def test_an_edition_without_a_numbered_basmala_gets_no_fatiha_entry(specials_path):
    specials.sync_from_detailed(
        RECITER,
        {1: [_row("1:1:1-1:1:4", 120, 3240), _row("1:2:1-1:2:4", 3240, 6905)]},
        generated_at="t",
        riwayah="warsh",
    )
    assert _chapter_1(specials_path) == []
