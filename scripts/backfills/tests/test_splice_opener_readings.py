"""``splice_opener_readings`` — openers go in front of the verse readings, numbered like a full run."""

from __future__ import annotations

from splice_opener_readings import has_openers, splice

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
