"""specials.json: bucket round-trip, release projection, and the cut's emission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from qua_jobs import cut_release
from qua_shared.schemas.bucket.specials import SpecialEntry, SpecialsDoc
from qua_shared.specials import default_basmala, project_release
from qua_shared.timestamps_native import is_verse_ref

GOLDEN = Path(__file__).resolve().parents[1] / "schemas" / "bucket" / "fixtures" / "specials.golden.json"


def _doc() -> SpecialsDoc:
    return SpecialsDoc.model_validate_json(GOLDEN.read_bytes())


def test_round_trip_is_byte_equal():
    raw = GOLDEN.read_text(encoding="utf-8").strip()
    assert _doc().model_dump_json() == raw


def test_unknown_field_is_rejected():
    body = json.loads(GOLDEN.read_bytes())
    body["chapters"]["2"][0]["tier"] = "high"
    with pytest.raises(ValidationError):
        SpecialsDoc.model_validate(body)


@pytest.mark.parametrize(
    "patch",
    [
        {"end_ms": 100, "start_ms": 100},  # empty span
        {"type": "Ruku"},  # not a special
    ],
)
def test_bad_entry_is_rejected(patch):
    base = {"type": "Basmala", "start_ms": 100, "end_ms": 900, "source": "align"}
    with pytest.raises(ValidationError):
        SpecialEntry(**{**base, **patch})


def test_chapters_must_be_time_ordered():
    a = {"type": "Basmala", "start_ms": 5000, "end_ms": 9000, "source": "align"}
    b = {"type": "Isti'adha", "start_ms": 0, "end_ms": 4000, "source": "align"}
    with pytest.raises(ValidationError):
        SpecialsDoc(generated_at="x", chapters={"2": [a, b]})


def test_same_voice_prefers_the_maintainer():
    entry = _doc().chapters["1"][1]
    assert entry.voice_verdict == "needs_confirmation"
    assert entry.same_voice is False  # voice_confirmed wins
    assert _doc().chapters["2"][0].same_voice is True


def test_release_projects_openers_only_for_shipped_chapters():
    rel = project_release(_doc(), "r", chapters={1, 2})
    assert set(rel.chapters) == {"1", "2"}  # 50 not shipped
    one = rel.chapters["1"]
    assert one.istiadha is not None and one.basmala is not None
    assert one.basmala.same_voice is False
    two = rel.chapters["2"]
    assert two.istiadha is None  # Sadaqa (post) is never released
    assert two.basmala.words[0] == [1, 360, 890]


def test_default_basmala_is_own_voice_and_unclipped():
    pick = default_basmala(_doc(), chapters=[1, 2, 50])
    assert pick is not None and (pick.chapter, pick.start_ms, pick.end_ms) == (2, 240, 6400)
    assert default_basmala(_doc(), chapters=[1, 50]) is None


def test_cut_emits_specials_json_for_shipped_chapters(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    (tmp_path / "reciters" / "r").mkdir(parents=True)
    (tmp_path / "reciters" / "r" / "specials.json").write_bytes(GOLDEN.read_bytes())
    body = json.loads(cut_release._build_specials_json("r", {2, 50}))
    assert body["slug"] == "r" and set(body["chapters"]) == {"2", "50"}
    assert "voice_z" not in json.dumps(body) and "source" not in json.dumps(body)
    assert body["default_basmala"] == {"chapter": 2, "start_ms": 240, "end_ms": 6400}
    assert "same_voice" not in body["chapters"]["50"]["basmala"]  # unknown → absent


def test_cut_without_specials_file_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    assert cut_release._build_specials_json("missing", {1}) is None


@pytest.mark.parametrize(
    ("ref", "verse"),
    [("2:1", True), ("114:6", True), ("0:0", False), ("2:0", False), ("Basmala", False), ("0:1", False)],
)
def test_only_real_verse_refs_project(ref, verse):
    assert is_verse_ref(ref) is verse
