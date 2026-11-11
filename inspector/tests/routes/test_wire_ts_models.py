"""Phase-4 regression net: the ``qua_shared.schemas.wire.timestamps`` models
validate the LIVE ``/api/ts/*`` + ``/api/static/catalog.json`` wire bodies.

Each "live" test hits the route through the Flask test client, validates the
response with the new model, and (for ``extra="forbid"`` models) asserts the
model's ``model_dump`` reproduces the exact key set the route emitted — so a
Phase-5 route refactor that drifts the shape fails here.

Shapes with no live route today (the deprecated reciter list + the verse
payload the FE assembles client-side from a shard) are exercised with a
representative inline fixture.

NOTE on the catalog: ``/api/static/catalog.json`` serves the FULL
``ReciterCatalog`` dump, while ``TsCatalogResponse`` is the slim Timestamps-tab
projection (``extra="ignore"``). The catalog test therefore validates that the
projection parses the real body and that the projected fields match — it does
NOT assert key-set equality (the route emits more keys by design).
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from qua_shared.schemas import (
    AudioCategory,
    Delivery,
    Letter,
    PhonemeInterval,
    ReciterEntry,
    TsCatalogResponse,
    TsConfigResponse,
    TsManifestResponse,
    TsReciter,
    TsVbrResponse,
    TsVerseData,
    TsWord,
)
from qua_shared.schemas.wire.timestamps import TsRecitersResponse

# ---------------------------------------------------------------------------
# GET /api/ts/config  (live)
# ---------------------------------------------------------------------------


def test_ts_config_model_validates_live_response(flask_client):
    """The model parses the live ``/api/ts/config`` body and round-trips its
    exact key set — proving ``mode`` is absent and ``catalog_url`` is present."""
    res = flask_client.get("/api/ts/config")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()

    model = TsConfigResponse.model_validate(body)
    dumped = model.model_dump(exclude_none=True)
    assert set(dumped.keys()) == set(body.keys())

    # The route does NOT emit ``mode`` and DOES emit ``catalog_url`` — pin both
    # so a regression is loud.
    assert "mode" not in body
    assert body["catalog_url"] == "/api/static/catalog.json"


# ---------------------------------------------------------------------------
# GET /api/ts/manifest  (live, gzipped body)
# ---------------------------------------------------------------------------


def _seed_released_reciter_with_catalog(slug: str = "rec_ts") -> None:
    """Seed a catalog reciter+delivery AND flip the reciter to ``released`` so
    the manifest build advertises it (complete by_surah → ts_chapters 1..114)."""
    from tests.conftest import _seed_catalog, _seed_state

    _seed_catalog(
        reciters=[ReciterEntry(reciter_id="rec_ts", name_en="Reciter TS")],
        deliveries=[
            Delivery(
                slug=slug,
                reciter_id="rec_ts",
                riwayah="hafs",
                style="mur",
                source="src",
                channel="ch",
                audio_category=AudioCategory.BY_SURAH,
                chapter_count=114,
                added_at=datetime.now(UTC),
                added_by_hf_id="seed",
            )
        ],
    )
    _seed_state(slug, state="released")


def test_ts_manifest_model_validates_live_response(flask_client, tmp_reciter_dir):
    """The model parses the decompressed live manifest body and reproduces its
    key set, including the per-reciter block (which must NOT carry ``_build``)."""
    from services.reference import timestamps as ts_serve

    _seed_released_reciter_with_catalog("rec_ts")
    ts_serve.invalidate()  # drop any manifest cached at an earlier db_seq

    res = flask_client.get("/api/ts/manifest")
    assert res.status_code == 200
    body = json.loads(gzip.decompress(res.get_data()).decode("utf-8"))

    model = TsManifestResponse.model_validate(body)
    dumped = model.model_dump(exclude_none=True)
    assert set(dumped.keys()) == set(body.keys())

    # The seeded reciter is advertised; its block must round-trip key-for-key.
    # The route ALWAYS emits ``name_ar`` (as null here), so compare the full
    # dump (NOT exclude_none) — the block has no genuinely-optional keys.
    assert "rec_ts" in body["reciters"], body["reciters"]
    rec_body = body["reciters"]["rec_ts"]
    rec_dumped = model.reciters["rec_ts"].model_dump()
    assert set(rec_dumped.keys()) == set(rec_body.keys())
    assert rec_body["name_ar"] is None
    # Route never emits a build-internal ``_build`` block on this read path.
    assert "_build" not in rec_body
    # Complete by_surah → derived 1..114.
    assert rec_body["ts_chapters"] == list(range(1, 115))


def test_ts_manifest_empty_when_no_released_reciters(flask_client, tmp_reciter_dir):
    """An empty published set still produces a model-valid manifest (no reciters)."""
    from services.reference import timestamps as ts_serve

    ts_serve.invalidate()
    res = flask_client.get("/api/ts/manifest")
    assert res.status_code == 200
    body = json.loads(gzip.decompress(res.get_data()).decode("utf-8"))

    model = TsManifestResponse.model_validate(body)
    assert model.reciters == {}
    assert set(model.model_dump(exclude_none=True).keys()) == set(body.keys())


# ---------------------------------------------------------------------------
# GET /api/static/catalog.json  (live; slim projection over the full catalog)
# ---------------------------------------------------------------------------


def test_ts_catalog_projection_parses_live_full_catalog(flask_client, tmp_reciter_dir):
    """The slim ``TsCatalogResponse`` projection parses the FULL catalog the
    route serves and surfaces the projected reciter/delivery fields.

    Key-set equality is intentionally NOT asserted — the route emits the whole
    ``ReciterCatalog`` (vocab/aliases/derived/generated_at + extra delivery
    fields) and the projection ignores the surplus.
    """
    _seed_released_reciter_with_catalog("rec_ts")

    res = flask_client.get("/api/static/catalog.json")
    assert res.status_code == 200
    body = res.get_json()

    # The real body carries keys the projection drops — proves it is a subset.
    assert "vocab" in body
    assert "derived" in body

    model = TsCatalogResponse.model_validate(body)
    assert model.schema_version >= 1
    assert [r.reciter_id for r in model.reciters] == ["rec_ts"]
    deliv = {d.slug: d for d in model.deliveries}
    assert "rec_ts" in deliv
    assert deliv["rec_ts"].audio_category == AudioCategory.BY_SURAH
    assert deliv["rec_ts"].reciter_id == "rec_ts"


def test_ts_catalog_empty_store_parses(flask_client, tmp_reciter_dir):
    """A fresh empty store still validates as the slim projection."""
    res = flask_client.get("/api/static/catalog.json")
    assert res.status_code == 200
    model = TsCatalogResponse.model_validate(res.get_json())
    assert model.reciters == []
    assert model.deliveries == []


# ---------------------------------------------------------------------------
# GET /api/ts/vbr/<reciter>  (live)
# ---------------------------------------------------------------------------


def test_ts_vbr_model_validates_live_response(flask_client, monkeypatch):
    """The model parses the live VBR body and reproduces its key set."""
    from routes.timestamps import timestamps as ts_routes

    from tests.conftest import _seed_state

    _seed_state("rec_a", state="released")

    monkeypatch.setattr(
        ts_routes,
        "vbr_chapters_for_reciter",
        lambda reciter: [2, 7] if reciter == "rec_a" else [],
    )

    res = flask_client.get("/api/ts/vbr/rec_a")
    assert res.status_code == 200
    body = res.get_json()

    model = TsVbrResponse.model_validate(body)
    assert model.vbr_chapters == [2, 7]
    # ``error`` is unset on the success path → dropped on dump → exact key match.
    assert set(model.model_dump(exclude_none=True).keys()) == set(body.keys())


# ---------------------------------------------------------------------------
# Reciter list (deprecated) — list[TsReciter]  (inline fixture, no live route)
# ---------------------------------------------------------------------------


def test_ts_reciters_response_validates_inline_fixture():
    fixture = [
        {"slug": "rec_a", "name": "Reciter A", "audio_source": "mp3quran", "has_data": True},
        {"slug": "rec_b", "name": "Reciter B"},
    ]
    adapter = TypeAdapter(TsRecitersResponse)
    parsed = adapter.validate_python(fixture)
    assert [r.slug for r in parsed] == ["rec_a", "rec_b"]
    assert isinstance(parsed[0], TsReciter)
    # exclude_none drops the unset optionals → exact per-entry key match.
    dumped = adapter.dump_python(parsed, exclude_none=True)
    assert dumped[0].keys() == fixture[0].keys()
    assert dumped[1].keys() == fixture[1].keys()


# ---------------------------------------------------------------------------
# Verse payload (legacy /api/ts/data shape)  (inline fixture, no live route)
# ---------------------------------------------------------------------------


def _verse_fixture(*, time_start_ms: int) -> dict:
    return {
        "reciter": "rec_a",
        "chapter": 1,
        "verse_ref": "1:1",
        "audio_url": "https://cdn.example/1.mp3",
        "audio_category": "by_surah_audio",
        "time_start_ms": time_start_ms,
        "time_end_ms": 1240,
        "intervals": [
            {"phone": "b", "start": 0.10, "end": 0.18},
            {"phone": "s", "start": 0.18, "end": 0.28, "bridge": "idgham"},
        ],
        "words": [
            {
                "location": "1:1:1",
                "text": "بِسْمِ",
                "start": 0.10,
                "end": 0.54,
                "phoneme_indices": [0, 1],
                "letters": [
                    {"char": "ب", "start": 0.10, "end": 0.18},
                    {"char": "س", "start": 0.18, "end": 0.28},
                ],
            }
        ],
    }


def test_ts_verse_data_validates_inline_fixture():
    fixture = _verse_fixture(time_start_ms=120)
    model = TsVerseData.model_validate(fixture)
    assert model.audio_category == "by_surah_audio"
    assert isinstance(model.words[0], TsWord)
    assert isinstance(model.words[0].letters[0], Letter)
    assert isinstance(model.intervals[1], PhonemeInterval)
    assert model.intervals[1].bridge == "idgham"
    # Round-trips shape-equal with exclude_none (the fixture omits the unset
    # geminate/bridge optionals on the first interval).
    dumped = json.loads(json.dumps(model.model_dump(exclude_none=True)))
    assert dumped == fixture


def test_ts_letter_preserves_explicit_null_timing():
    """A ``null`` per-letter timing is a meaningful value (aligner couldn't
    place the letter) and survives parse → dump."""
    letter = Letter.model_validate({"char": "س", "start": 0.18, "end": None})
    assert letter.end is None
    # The explicit null is retained when not excluding none.
    assert letter.model_dump() == {"char": "س", "start": 0.18, "end": None}


def test_ts_verse_data_allows_negative_time_start_ms():
    """``time_start_ms`` must accept a negative value (by_surah chapter-offset
    relative) — i.e. it carries NO ``ge=0`` bound."""
    model = TsVerseData.model_validate(_verse_fixture(time_start_ms=-350))
    assert model.time_start_ms == -350


def test_ts_verse_data_rejects_unknown_audio_category():
    """``audio_category`` is the ``*_audio`` long-form Literal; the short
    shard/manifest form is rejected (the two are distinct fields)."""
    bad = _verse_fixture(time_start_ms=0)
    bad["audio_category"] = "by_surah"  # short form — wrong for this shape
    with pytest.raises(ValueError):
        TsVerseData.model_validate(bad)
