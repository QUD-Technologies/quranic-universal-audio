from __future__ import annotations

import orjson

from qua_shared.schemas import TsShardDoc
from qua_shared.timestamps_shards import brotli_shard
from services import storage_paths
from services.storage import data_dir
from services.storage.hf_bucket import get_backend

SLUG = "roundtrip_reciter"


def _document() -> dict:
    return {
        "_meta": {
            "schema_version": 15,
            "chapter": 1,
            "audio_category": "by_surah",
            "phonemizer_version": "2.15",
            "native_schema_version": 2,
            "renderer_codec_version": 1,
            "native_profile": {
                "riwayah": "hafs",
                "script": "uthmani",
                "variant": {},
                "extra_phonemes": [],
            },
        },
        "readings": [],
    }


def test_native_read_path_is_byte_passthrough(tmp_reciter_dir):
    document = _document()
    body = brotli_shard(document)
    get_backend().write_bytes_atomic(storage_paths.timestamps_path_br(SLUG, 1), body)
    assert data_dir.read_timestamps_chapter_br(SLUG, 1) == body
    inflated = data_dir.read_timestamps_chapter(SLUG, 1)
    assert inflated is not None
    parsed = orjson.loads(inflated)
    assert TsShardDoc.model_validate(parsed).model_dump(by_alias=True) == document


def test_missing_chapter_returns_none(tmp_reciter_dir):
    assert data_dir.read_timestamps_chapter_br(SLUG, 99) is None
    assert data_dir.read_timestamps_chapter(SLUG, 99) is None
