"""Release cuts omit drifted chapters from every artifact and retain the rest."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from contextlib import nullcontext

import brotli
import pytest

from qua_jobs import cut_release
from qua_shared.audio import upstream


def _manifest(root, slug, chapters):
    path = root / "catalog" / "audio_manifest" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chapters": chapters}), encoding="utf-8")


def test_lists_only_recitations_with_a_replaced_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    entry = {"size_bytes": 1000, "duration_sec": 465}
    _manifest(tmp_path, "swapped", {"62": {**entry, "url": "https://cdn/swapped/62.mp3"}})
    _manifest(tmp_path, "intact", {"1": {**entry, "url": "https://cdn/intact/1.mp3"}})
    probes = {
        "https://cdn/swapped/62.mp3": {"size": 800, "duration_s": 321.0},
        "https://cdn/intact/1.mp3": {"size": 1000, "duration_s": 465.0},
    }
    monkeypatch.setattr(upstream, "probe", probes.__getitem__)

    changed = cut_release._upstream_changes(
        [{"slug": "swapped"}, {"slug": "intact"}, {"slug": "no_manifest"}]
    )

    assert changed == {"swapped": {62: "ch62 recording (465s → 321s)"}}


def _timestamps(root, slug, chapter):
    ref = f"{chapter}:1"
    readings = []
    for index in range(2):
        start, end = 100 + index * 400, 200 + index * 400
        readings.append(
            {
                "id": f"r{index}",
                "parts": [[ref, start, end, 0, 1]],
                "render": {
                    "v": 1,
                    "m": ["test", "canon", "native"],
                    "p": [],
                    "r": [],
                    "w": [[f"{ref}:1", "x", [], [], [], [], []]],
                    "b": [[1, [], [], [], None, None]],
                    "a": [[0, [100], [200], [200], "x", [], 0, None]],
                },
                "timing": {"w": [[start, end]], "s": [], "a": [[start, end]], "c": []},
            }
        )
    shard = {
        "_meta": {
            "schema_version": 13,
            "chapter": chapter,
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
        "readings": readings,
    }
    path = root / "reciters" / slug / "timestamps" / f"{chapter}.json.br"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(brotli.compress(json.dumps(shard).encode()))
    return path


@pytest.mark.parametrize("corrupt_dropped_shard", [False, True])
def test_built_member_omits_dropped_chapter_everywhere(
    tmp_path, monkeypatch, corrupt_dropped_shard
):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    _timestamps(tmp_path, "swapped", 1)
    dropped_path = _timestamps(tmp_path, "swapped", 62)
    if corrupt_dropped_shard:
        dropped_path.write_bytes(b"unreadable shard")
    _manifest(
        tmp_path,
        "swapped",
        {
            "1": {"url": "https://cdn/1.mp3", "source_offset_ms": 20},
            "62": {"url": "local/audio/62.mp3", "source_offset_ms": 800},
        },
    )
    ctx = cut_release._BuildContext(
        surah_info={
            str(ch): {"num_verses": 1, "verses": [{"verse": 1, "num_words": 1}]} for ch in (1, 62)
        },
        digital_khatt_words={f"{ch}:1:1": {"text": "x"} for ch in (1, 62)},
        script_sha256="0" * 64,
        prior_members={},
        pads={"pad_start": 0, "pad_end": 0, "min_gap": 0},
        dropped_upstream_chapters={"swapped": {62: "ch62 recording (465s → 321s)"}},
    )
    member = cut_release._build_member(
        {"slug": "swapped", "ts_version": "ts1", "chapter_count": 2}, ctx
    )
    assert member is not None
    assert member["coverage_ayahs"] == member["coverage_surahs"] == 1
    assert member["missing_surahs"] == "62"
    assert member["missing_verses"] == ""
    assert member["_validation"]["violation_count"] == 0
    catalog = member["catalog_snapshot"]
    assert catalog["audio"]["chapter_urls"] == {"1": "https://cdn/1.mp3"}
    assert catalog["audio"]["chapter_offsets_ms"] == {"1": 20}
    assert catalog["coverage"] == {"surahs": 1, "ayahs": 1, "missing_surahs": "62"}
    zip_bytes = cut_release._pack_recitation_zip("swapped", member["_files"])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert json.loads(archive.read("catalog.json")) == catalog
        for tier in member["tiers"]:
            doc = json.loads(gzip.decompress(archive.read(f"{tier}_timestamps.json.gz")))
            assert [row[0] for row in doc["rows"]] == ["1:1", "1:1"]
            assert doc["_meta"]["verse_count"] == 1
            assert doc["_meta"]["occurrence_count"] == 2
    dataset_catalog = json.loads(cut_release._build_dataset_level_catalog([member]))
    assert dataset_catalog["recitations"] == [catalog]
    member.update(zip_sha256=cut_release._sha256_hex(zip_bytes), zip_bytes=len(zip_bytes))
    manifest = json.loads(
        cut_release._build_dataset_manifest(
            "v1.0.0", None, [member], {}, {}, "owner", "repo", "2026-10-11T00:00:00Z"
        )
    )
    assert manifest["recitation_count"] == 1
    assert manifest["recitations"]["swapped"]["coverage_ayahs"] == 1
    changelog = cut_release._build_changelog(
        "v1.0.0", None, [member], [], "owner", "repo", "11-10-2026", "dataset"
    ).decode()
    assert "| 1 ayahs | surahs 62 |" in changelog


def test_legacy_ayah_audio_sources_exclude_the_whole_chapter():
    urls, offsets = cut_release._audio_sources_from_manifest(
        "slug",
        {"1:1": "https://cdn/1-1.mp3", "62:1": "https://cdn/62-1.mp3"},
        excluded_chapters=frozenset({62}),
    )
    assert urls == {"1:1": "https://cdn/1-1.mp3"}
    assert offsets == {}


def _main_inputs(tmp_path, monkeypatch, eligible, dropped):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    monkeypatch.delenv("RELEASE_VERSION", raising=False)
    monkeypatch.setattr(cut_release, "_preflight", lambda: 0)
    monkeypatch.setattr(cut_release, "_repo_owner_name", lambda: ("owner", "repo", "dataset"))
    monkeypatch.setattr(cut_release, "_open_inspector_db_readonly", lambda: nullcontext(None))
    monkeypatch.setattr(cut_release, "_eligible_recitations", lambda conn: eligible)
    monkeypatch.setattr(cut_release, "_prior_release_members", lambda conn: (None, {}))
    monkeypatch.setattr(cut_release, "_upstream_changes", lambda rows: dropped)
    posted = []
    monkeypatch.setattr(cut_release, "_post_webhook", lambda **kw: posted.append(kw) or True)
    return posted


def test_every_chapter_dropped_aborts_before_build(tmp_path, monkeypatch):
    _timestamps(tmp_path, "swapped", 62)
    posted = _main_inputs(
        tmp_path,
        monkeypatch,
        [{"slug": "swapped"}],
        {"swapped": {62: "ch62 recording (465s → 321s)"}},
    )
    assert cut_release.main() == 3
    assert posted[0]["status"] == "failed"
    assert posted[0]["members"] == []
    assert posted[0]["validation_summary"] == {
        "dropped_upstream_chapters": {"swapped": ["ch62 recording (465s → 321s)"]}
    }


def test_member_with_every_chapter_dropped_is_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    _timestamps(tmp_path, "swapped", 62)
    ctx = cut_release._BuildContext(
        surah_info={},
        digital_khatt_words={},
        script_sha256="0" * 64,
        prior_members={},
        pads=cut_release.pad_params_from_env(),
        dropped_upstream_chapters={"swapped": {62: "changed"}},
    )
    assert cut_release._build_member({"slug": "swapped", "ts_version": "ts1"}, ctx) is None


@pytest.mark.parametrize("with_drift", [False, True])
def test_successful_cut_retains_partial_recitations_and_reports_exclusions(
    tmp_path, monkeypatch, with_drift
):
    for slug, chapters in (("partial", (1, 62)), ("held", (62,))):
        for chapter in chapters:
            _timestamps(tmp_path, slug, chapter)
        _manifest(tmp_path, slug, {str(ch): {"url": f"https://cdn/{ch}.mp3"} for ch in chapters})
    dropped = {slug: {62: "changed"} for slug in ("partial", "held")} if with_drift else {}
    posted = _main_inputs(
        tmp_path,
        monkeypatch,
        [{"slug": slug, "ts_version": "ts1"} for slug in ("partial", "held")],
        dropped,
    )
    refs = tmp_path / "data"
    refs.mkdir()
    surah_info = {
        str(ch): {"num_verses": 1, "verses": [{"verse": 1, "num_words": 1}]} for ch in (1, 62)
    }
    (refs / "surah_info.json").write_text(json.dumps(surah_info))
    helpers = tmp_path / "qua_jobs"
    helpers.mkdir()
    for name in ("shard.py", "check_updates.py", "download_audio.py"):
        (helpers / name).write_text("")
    script = json.dumps({f"{ch}:1:1": {"text": "x"} for ch in (1, 62)}).encode()
    monkeypatch.setattr(cut_release, "_code_root", lambda: tmp_path)
    monkeypatch.setattr(cut_release, "_load_digital_khatt_assets", lambda root: (script, b"font"))
    monkeypatch.setenv(cut_release.BUILD_WORKERS_ENV, "1")
    monkeypatch.setattr(cut_release, "_gh_release_exists", lambda *a: False)
    monkeypatch.setattr(
        cut_release,
        "_gh_create_release",
        lambda *a, **kw: {"upload_url": "https://uploads", "html_url": "https://release"},
    )
    uploads = {}
    monkeypatch.setattr(
        cut_release, "_gh_upload_asset", lambda url, name, data, *a: uploads.update({name: data})
    )
    assert cut_release.main() == 0
    assert len(posted) == 1
    assert posted[0]["validation_summary"]["dropped_upstream_chapters"] == {
        slug: list(chapters.values()) for slug, chapters in dropped.items()
    }
    assert "held_upstream_changes" not in posted[0]["validation_summary"]
    members = posted[0]["members"]
    assert [m["slug"] for m in members] == (["partial"] if with_drift else ["partial", "held"])
    assert members[0]["coverage_ayahs"] == (1 if with_drift else 2)
    manifest = json.loads(uploads["manifest.json"])
    assert manifest["recitation_count"] == (1 if with_drift else 2)
    assert ("held.zip" in uploads) == (not with_drift)
    assert "partial.zip" in uploads
