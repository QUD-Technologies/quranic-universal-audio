"""Regression for issue #279: a batch publishes every slug under one JOB_ID in
one container, and each slug's Dataset must carry its OWN rows — never a cached
copy of an earlier slug's."""

from __future__ import annotations

from qua_jobs import publish_hf


def _rows(source_url: str) -> list[dict]:
    return [
        {
            "surah": 1,
            "ayah": 1,
            "duration_ms": 1000,
            "text_uthmani": "x",
            "segments": [[0, 1000]],
            "word_timestamps": [[1, 0, 1000]],
            "source_url": source_url,
            "clip_start": 0,
        }
    ]


def test_batch_slugs_sharing_job_id_and_cache_get_their_own_rows(monkeypatch, tmp_path):
    from datasets.features.audio import Audio

    # Bytes pass straight through; the real encoder only needs torchcodec to
    # validate them, which this cache-keying test doesn't exercise.
    monkeypatch.setattr(
        Audio, "encode_example", lambda self, v: {"bytes": v["bytes"], "path": v["path"]}
    )
    monkeypatch.setenv("JOB_ID", "batch-job")
    # Relative + short: datasets embeds the cache path in its lock filename,
    # which overflows Windows' MAX_PATH under a pytest tmp_path.
    monkeypatch.chdir(tmp_path)
    cache_dir = "c"

    first = publish_hf._build_hf_dataset(
        "reciter_a",
        "hafs_an_asim",
        _rows("https://a.example/001.mp3"),
        [b"AAAA"],
        cache_dir=cache_dir,
    )
    second = publish_hf._build_hf_dataset(
        "reciter_b",
        "hafs_an_asim",
        _rows("https://b.example/001.mp3"),
        [b"BBBB"],
        cache_dir=cache_dir,
    )

    def stored(ds) -> tuple[str, bytes]:
        # Read the raw Arrow cells: indexing the Dataset would DECODE the audio.
        row = ds.data.slice(0, 1).to_pylist()[0]
        return row["source_url"], row["audio"]["bytes"]

    assert stored(first) == ("a.example/001.mp3", b"AAAA")
    assert stored(second) == ("b.example/001.mp3", b"BBBB")
