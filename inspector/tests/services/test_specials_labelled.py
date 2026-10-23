"""A special a reviewer labels in detailed.json (Edit Ref → ``Basmala`` …) is mirrored into specials.json."""

from __future__ import annotations

import json

import pytest

from qua_shared.schemas.bucket.specials import SpecialsDoc
from services.segments import save
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
