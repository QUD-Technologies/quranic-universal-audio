"""Timestamps verse route — one ayah of a chapter, sliced from its shard."""

from __future__ import annotations

import brotli
import orjson
import pytest


def _shard(*readings: dict) -> bytes:
    return brotli.compress(
        orjson.dumps({"_meta": {"schema_version": 13, "chapter": 2}, "readings": list(readings)}),
        quality=1,
    )


@pytest.fixture
def chapter(monkeypatch):
    """A three-reading chapter: verse 1, a reading spanning 1-2, then verse 3."""
    from services.reference import timestamps as ts_serve

    body = _shard(
        {"id": "r1", "parts": [["2:1", 0, 100, 0, 1]]},
        {"id": "r2", "parts": [["2:1", 100, 200, 2, 3], ["2:2", 200, 300, 0, 1]]},
        {"id": "r3", "parts": [["2:3", 300, 400, 0, 1]]},
    )
    monkeypatch.setattr(ts_serve, "shard_bytes", lambda *a, **k: body)
    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()
    yield
    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()


def _read(res) -> dict:
    return orjson.loads(brotli.decompress(res.data))


def test_verse_serves_one_ayah_as_a_shard_document(flask_client, chapter):
    """The body decodes with the chapter decoder: same ``_meta``, fewer readings."""
    res = flask_client.get("/api/ts/verse/reciter_a/2?ayah=3")

    assert res.status_code == 200
    assert res.headers["Content-Encoding"] == "br"
    assert res.mimetype == "application/json"
    doc = _read(res)
    assert doc["_meta"]["schema_version"] == 13
    assert [r["id"] for r in doc["readings"]] == ["r3"]


def test_verse_carries_the_chapter_ayah_list(flask_client, chapter):
    """A verse picker learns what the chapter offers without fetching it."""
    doc = _read(flask_client.get("/api/ts/verse/reciter_a/2?ayah=1"))

    assert doc["ayah"] == 1
    assert doc["ayahs"] == [1, 2, 3]


def test_verse_keeps_a_reading_that_spans_two_ayahs(flask_client, chapter):
    """Splitting a shared reading would mean re-timing its cells; the client
    narrows it to the verse it asked for instead."""
    assert [
        r["id"] for r in _read(flask_client.get("/api/ts/verse/reciter_a/2?ayah=1"))["readings"]
    ] == [
        "r1",
        "r2",
    ]
    assert [
        r["id"] for r in _read(flask_client.get("/api/ts/verse/reciter_a/2?ayah=2"))["readings"]
    ] == ["r2"]


def test_verse_without_ayah_serves_the_first_timed_one(flask_client, chapter):
    """A chapter change asks once, rather than asking what exists and then for it."""
    doc = _read(flask_client.get("/api/ts/verse/reciter_a/2"))

    assert doc["ayah"] == 1
    assert [r["id"] for r in doc["readings"]] == ["r1", "r2"]


def test_verse_the_chapter_does_not_time_is_404(flask_client, chapter):
    assert flask_client.get("/api/ts/verse/reciter_a/2?ayah=9").status_code == 404


def test_non_numeric_ayah_is_400(flask_client, chapter):
    assert flask_client.get("/api/ts/verse/reciter_a/2?ayah=last").status_code == 400


def test_missing_chapter_is_404(flask_client, monkeypatch):
    from services.reference import timestamps as ts_serve

    monkeypatch.setattr(ts_serve, "shard_bytes", lambda *a, **k: None)

    assert flask_client.get("/api/ts/verse/reciter_a/2?ayah=1").status_code == 404


def test_chapter_is_parsed_once_for_every_verse_in_it(monkeypatch):
    """The whole point of the route: the inflate + parse is per chapter, not
    per verse. A reader walking a chapter must not re-pay it each ayah."""
    from services.reference import timestamps as ts_serve

    body = _shard(
        {"id": "r1", "parts": [["2:1", 0, 100, 0, 1]]},
        {"id": "r2", "parts": [["2:2", 100, 200, 0, 1]]},
    )
    monkeypatch.setattr(ts_serve, "shard_bytes", lambda *a, **k: body)
    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()

    calls = 0
    real = ts_serve.orjson.loads

    def counting(raw):
        nonlocal calls
        calls += 1
        return real(raw)

    monkeypatch.setattr(ts_serve.orjson, "loads", counting)
    assert ts_serve.verse_bytes("reciter_a", 2, 1) is not None
    assert ts_serve.verse_bytes("reciter_a", 2, 2) is not None
    assert calls == 1

    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()


def test_a_part_ref_with_no_ayah_is_skipped(monkeypatch):
    """A basmala part belongs to no verse and must not be filed under one."""
    from services.reference import timestamps as ts_serve

    body = _shard(
        {"id": "r0", "parts": [["basmala", 0, 50, 0, 1]]},
        {"id": "r1", "parts": [["2:1", 50, 100, 0, 1]]},
    )
    monkeypatch.setattr(ts_serve, "shard_bytes", lambda *a, **k: body)
    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()

    doc = orjson.loads(brotli.decompress(ts_serve.verse_bytes("reciter_a", 2)))
    assert doc["ayahs"] == [1]
    assert [r["id"] for r in doc["readings"]] == ["r1"]

    ts_serve._slice_lru.clear()
    ts_serve._verse_lru.clear()
