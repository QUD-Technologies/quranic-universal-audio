"""Shard-integrity sweep — the vanished-shard detector.

The failure it guards against is silent (audio, peaks and detailed.json all stay
intact, and coverage_report.json still reads clean), so the interesting cases are
the ones that must NOT fire: an untimestamped reciter, a stale orphan beside a
healthy shard, and an unreadable directory.
"""

from __future__ import annotations

import pytest

from services.storage import shard_integrity


def _seed(tmp_path, slug, *, shards=(), audio=(), orphans=()):
    ts = tmp_path / "reciters" / slug / "timestamps"
    au = tmp_path / "reciters" / slug / "audio"
    ts.mkdir(parents=True, exist_ok=True)
    au.mkdir(parents=True, exist_ok=True)
    for ch in shards:
        (ts / f"{ch}.json.br").write_bytes(b"x")
    for ch in audio:
        (au / f"{ch}.mp3").write_bytes(b"x")
    for ch in orphans:
        (ts / f".{ch}.json.br.ntwm4f0q").write_bytes(b"x")


def test_orphan_temp_is_reported_as_recoverable(tmp_reciter_dir, tmp_path):
    _seed(tmp_path, "r", shards=(101, 103), audio=(101, 102, 103), orphans=(102,))

    findings, unreadable = shard_integrity.scan(["r"])

    assert unreadable == []
    assert len(findings) == 1
    f = findings[0]
    assert (f.slug, f.chapter, f.kind) == ("r", 102, "orphan_temp")
    assert f.orphan_path == "reciters/r/timestamps/.102.json.br.ntwm4f0q"


def test_missing_shard_without_orphan_is_unrecoverable(tmp_reciter_dir, tmp_path):
    _seed(tmp_path, "r", shards=(44, 46), audio=(44, 45, 46))

    findings, _ = shard_integrity.scan(["r"])

    assert [(f.chapter, f.kind, f.orphan_path) for f in findings] == [(45, "missing_shard", None)]


def test_untimestamped_reciter_is_not_a_finding(tmp_reciter_dir, tmp_path):
    """Zero shards = never timestamped (and absent from every release), not loss."""
    _seed(tmp_path, "r", audio=(1, 2, 3))

    findings, unreadable = shard_integrity.scan(["r"])

    assert findings == []
    assert unreadable == []


def test_stale_orphan_beside_a_healthy_shard_is_ignored(tmp_reciter_dir, tmp_path):
    """A write that failed and was later redone leaves litter, not a gap."""
    _seed(tmp_path, "r", shards=(1, 2), audio=(1, 2), orphans=(2,))

    findings, _ = shard_integrity.scan(["r"])

    assert findings == []


def test_audio_without_shard_only_counts_missing_chapters(tmp_reciter_dir, tmp_path):
    """A reciter who simply never recited a chapter has no audio for it either,
    so a shard-less chapter is only a finding when its audio exists."""
    _seed(tmp_path, "r", shards=(1,), audio=(1,))

    findings, _ = shard_integrity.scan(["r"])

    assert findings == []


def test_unreadable_delivery_reports_no_findings(tmp_reciter_dir, tmp_path, monkeypatch):
    """ "Could not look" must never render as "the data is gone" — a listing
    failure yields an unreadable slug and zero findings."""
    _seed(tmp_path, "ok", shards=(1,), audio=(1, 2))

    real = shard_integrity._list_strict

    def boom(path: str):
        if path.startswith("reciters/broken/"):
            raise OSError("bucket API down")
        return real(path)

    monkeypatch.setattr(shard_integrity, "_list_strict", boom)

    findings, unreadable = shard_integrity.scan(["broken", "ok"])

    assert unreadable == ["broken"]
    assert [(f.slug, f.chapter) for f in findings] == [("ok", 2)]


def test_source_key_is_stable_per_slug_chapter_kind():
    """The notification dedup key: an unrepaired gap must not re-notify daily."""
    a = shard_integrity.IntegrityFinding(slug="r", chapter=102, kind="orphan_temp")
    b = shard_integrity.IntegrityFinding(slug="r", chapter=102, kind="orphan_temp", orphan_path="x")
    assert a.source_key == b.source_key == "shardint:orphan_temp:r:102"
    assert (
        shard_integrity.IntegrityFinding(slug="r", chapter=102, kind="missing_shard").source_key
        != a.source_key
    )


@pytest.mark.parametrize("name", ["102.json", "102.json.br.tmp", "x.json.br", "102.JSON.BR"])
def test_non_shard_filenames_are_not_counted_as_shards(tmp_reciter_dir, tmp_path, name):
    """Only ``<n>.json.br`` counts; a near-miss must not mask a real gap."""
    _seed(tmp_path, "r", shards=(1,), audio=(1, 102))
    (tmp_path / "reciters" / "r" / "timestamps" / name).write_bytes(b"x")

    findings, _ = shard_integrity.scan(["r"])

    assert [(f.chapter, f.kind) for f in findings] == [(102, "missing_shard")]
