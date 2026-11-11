"""``public_source_url`` — the one rule both release adapters use (#285)."""

from __future__ import annotations

from qua_shared.audio.sources import chapters_without_public_source, public_source_url

BUCKET = "https://huggingface.co/buckets/o/b/resolve/reciters/x/audio/1.mp3"


def test_source_url_wins_over_url():
    entry = {"url": BUCKET, "source_url": "https://youtu.be/AAA"}
    assert public_source_url(entry) == "https://youtu.be/AAA"


def test_plain_cdn_url_is_public():
    assert public_source_url({"url": " https://cdn.example/1.mp3 "}) == "https://cdn.example/1.mp3"


def test_local_paths_and_bucket_links_are_not_public():
    assert public_source_url({"url": "/srv/scratch/x/001.mp3"}) is None
    assert public_source_url({"url": "reciters/x/audio/1.mp3"}) is None
    assert public_source_url({"url": BUCKET}) is None


def test_chapters_without_public_source_sorts_numerically():
    chapters = {
        "10": {"url": "/a"},
        "2": {"url": "/b"},
        "3": {"url": "https://cdn.example/3.mp3"},
    }
    assert chapters_without_public_source(chapters) == ["2", "10"]
