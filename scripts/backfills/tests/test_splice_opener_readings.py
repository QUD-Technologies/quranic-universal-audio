"""``splice_opener_readings`` — openers go in front of the verse readings, numbered like a full run."""

from __future__ import annotations

from splice_opener_readings import has_openers, splice, voice_refs

_META = {"chapter": 2, "variant_policy": "kept"}


def _reading(rid: str, part: str, special: str | None = None) -> dict:
    r = {"id": rid, "parts": [[part, 0, 1, 0, 1]]}
    return {**r, "special": special} if special else r


def test_openers_lead_and_ids_follow_reading_order():
    shard = {"_meta": _META, "readings": [_reading("r1", "2:1"), _reading("r2", "2:2")]}
    built = {"_meta": {"chapter": 2}, "readings": [_reading("r1", "0:2", "Basmala")]}
    out = splice(shard, built)
    assert [(r["id"], r["parts"][0][0], r.get("special")) for r in out["readings"]] == [
        ("r1", "0:2", "Basmala"),
        ("r2", "2:1", None),
        ("r3", "2:2", None),
    ]
    assert out["_meta"] == _META


def test_splicing_again_replaces_the_openers():
    shard = {"_meta": _META, "readings": [_reading("r1", "0:2", "Basmala"), _reading("r2", "2:1")]}
    built = {
        "_meta": {},
        "readings": [_reading("r1", "0:1", "Isti'adha"), _reading("r2", "0:2", "Basmala")],
    }
    out = splice(shard, built)
    assert [r["parts"][0][0] for r in out["readings"]] == ["0:1", "0:2", "2:1"]
    assert has_openers(out) and not has_openers({"readings": [_reading("r1", "2:1")]})


def test_voice_refs_take_the_first_long_verse_clips_and_skip_fatiha_basmala():
    def seg(ref, start, end):
        return {"matched_ref": ref, "time_start": start, "time_end": end}

    segments = [
        seg("1:2:1-1:2:4", 9000, 9500),  # too short
        seg("1:1:1-1:1:4", 4000, 8000),  # Al-Fatiha's Basmala
        seg("Isti'adha", 0, 3500),
        seg("", 8000, 9000),  # no match
        seg("1:3:1-1:3:2", 10000, 12000),
        seg("1:4:1-1:4:3", 13000, 15000),
    ]
    assert voice_refs(1, segments, frozenset({"Isti'adha", "Basmala"})) == [
        [10000, 12000],
        [13000, 15000],
    ]
