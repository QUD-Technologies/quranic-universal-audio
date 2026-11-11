"""``qua_jobs.bucket_io`` over the Hub API: a short download is retried, a
missing file is reported at once, uploads and deletes name bucket paths."""

from __future__ import annotations

from types import SimpleNamespace

import huggingface_hub
import pytest

from qua_jobs import bucket_io


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("BUCKET_REPO", "owner/bucket")
    monkeypatch.setattr(bucket_io, "RETRY_SLEEP_S", 0)
    calls: dict[str, list] = {"download": [], "batch": []}
    sizes = {"reciters/r/audio/201.mp3": 10}
    short = {"left": 1}

    def paths_info(repo, paths, token=None):
        return [SimpleNamespace(path=p, size=sizes[p]) for p in paths if p in sizes]

    def download(repo, files, raise_on_missing_files=False, token=None):
        for rel, dest in files:
            calls["download"].append(rel)
            n = sizes[rel] - (5 if short["left"] else 0)
            short["left"] = max(0, short["left"] - 1)
            with open(dest, "wb") as fh:
                fh.write(b"x" * n)

    def batch(repo, add=None, delete=None, token=None):
        calls["batch"].append((repo, [r for _l, r in add or []], delete))

    monkeypatch.setattr(huggingface_hub, "get_bucket_paths_info", paths_info)
    monkeypatch.setattr(huggingface_hub, "download_bucket_files", download)
    monkeypatch.setattr(huggingface_hub, "batch_bucket_files", batch)
    return calls


def test_a_short_download_is_retried_until_the_size_matches(hub, tmp_path):
    out = bucket_io.fetch("reciters/r/audio/201.mp3", tmp_path / "a.mp3")
    assert out.stat().st_size == 10
    assert hub["download"] == ["reciters/r/audio/201.mp3"] * 2


def test_a_missing_file_raises_without_retrying(hub, tmp_path):
    with pytest.raises(bucket_io.MissingFile):
        bucket_io.fetch("reciters/r/audio/999.mp3", tmp_path / "b.mp3")
    assert hub["download"] == []


def test_put_and_delete_use_bucket_paths(hub, tmp_path):
    local = tmp_path / "1.mp3"
    local.write_bytes(b"mp3")
    bucket_io.put(local, "reciters/r/audio/1.mp3")
    bucket_io.delete(["reciters/r/audio/201.mp3"])
    assert hub["batch"] == [
        ("owner/bucket", ["reciters/r/audio/1.mp3"], None),
        ("owner/bucket", [], ["reciters/r/audio/201.mp3"]),
    ]
