"""Slot files in the align pipeline — grouping, cutting, resolving, split stage.

Playlist files carry no chapters: the aligner detects which surahs each holds
and ``resolve`` assigns every surah to a file (stitching one uploaded in parts).
Cutting is exercised against a real aligner result (one file holding surahs
109–114, ``tests/fixtures/align/combined_109_114.json``); the split HF Job is
stubbed with a fake report so the stage's bookkeeping (staged chapters, manifest
offsets, dropped/adopted chapters) runs against an in-memory bucket.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qua_shared.audio.sources import SLOT_BASE, groups_from_manifest, needs_ytdlp
from qua_shared.schemas import (
    AudioCategory,
    Channel,
    Delivery,
    ReciterEntry,
    Riwayah,
    Source,
    Style,
    Vocab,
)
from services.admin.align_pipeline import partition, resolve

FIXTURE = Path(__file__).parents[1] / "fixtures" / "align" / "combined_109_114.json"
SLUG = "rec_comb"
DRIVE = "https://drive.google.com/file/d/1rl6qU2TnCacbR_VjK5V-wFm97Zn-cHSg/view"


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["segments"]


def _row(ref_from: str, ref_to: str, t0: float, t1: float, kind: str = "quran") -> dict:
    return {"kind": kind, "ref_from": ref_from, "ref_to": ref_to, "time_from": t0, "time_to": t1}


def _file(item: int, rows: list[dict], planned: tuple[int, ...] = ()) -> resolve.FileCuts:
    return resolve.FileCuts(
        item=item, url=f"https://src/{item}", planned=planned, cuts=partition.cut_file(rows, None)
    )


def _write_manifest(backend, chapters: dict, sources: list[dict] | None = None) -> None:
    backend.write_json_atomic(
        f"catalog/audio_manifest/{SLUG}.json",
        {
            "slug": SLUG,
            "chapters": chapters,
            "sources": sources or [],
            "_meta": {"checksum": "x", "chapter_count": len(chapters), "category": "by_surah"},
        },
    )


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------


def test_groups_put_combined_and_undetected_files_in_slots():
    groups = groups_from_manifest(
        {
            "3": {"url": "https://cdn/3.mp3"},
            "1": {"url": "u1", "source_url": DRIVE},
            "2": {"url": "u2", "source_url": DRIVE},
        },
        [{"url": DRIVE}, {"url": "https://www.youtube.com/watch?v=a"}],
    )
    assert [(g.chapters, g.slot, g.detect) for g in groups] == [
        ((1, 2), SLOT_BASE, False),
        ((3,), None, False),
        ((), SLOT_BASE + 1, True),  # DRIVE is already a chapter source: listed once
    ]
    assert [g.weight for g in groups] == [2, 1, 1]


@pytest.mark.parametrize(
    ("url", "ytdlp"),
    [
        (DRIVE, False),
        ("https://drive.google.com/uc?id=1rl6qU2TnCacbR_VjK5V&export=download", False),
        ("https://archive.org/download/x/001%20-%20a.mp3", False),
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://soundcloud.com/a/b", True),
    ],
)
def test_needs_ytdlp(url, ytdlp):
    assert needs_ytdlp(url) is ytdlp


# ---------------------------------------------------------------------------
# cutting (real aligner output)
# ---------------------------------------------------------------------------


def test_cut_file_finds_every_surah_without_overlap():
    cuts = partition.cut_file(_rows(), 255076)
    assert sorted(cuts) == [109, 110, 111, 112, 113, 114]
    ordered = [cuts[c] for c in sorted(cuts)]
    for a, b in zip(ordered, ordered[1:], strict=False):
        assert a.end_ms <= b.start_ms
    for cut in ordered:  # each opens on its own Basmala: specials attach forward
        assert cut.rows[0]["kind"] == "special"
        assert all(partition.row_surah(r) in (None, cut.chapter) for r in cut.rows)
    assert cuts[109].start_ms == round(4.46 * 1000) - partition.TRIM_PAD_MS
    assert cuts[114].end_ms <= 255076


def test_ayahs_and_matched_ms_read_the_refs():
    rows = [_row("2:5:1", "2:7:3", 0, 10), _row("", "", 10, 12), _row("2:8:1", "2:8:4", 12, 15)]
    assert partition.ayahs(rows) == {5, 6, 7, 8}
    assert partition.matched_ms(rows) == 13000
    assert partition.unmatched_ms(rows) == 2000


def test_rebase_moves_rows_onto_the_cut_timeline():
    cut = partition.cut_file(_rows(), None)[110]
    rebased = partition.rebase(cut.rows, cut.start_ms)
    assert rebased[0]["time_from"] == pytest.approx(cut.rows[0]["time_from"] - cut.start_ms / 1000)
    assert min(r["time_from"] for r in rebased) >= 0


def test_dominant_other_surah_flags_a_mislabelled_single_file():
    rows = [r for r in _rows() if partition.row_surah(r) == 113]
    assert partition.dominant_other_surah(94, rows) == 113
    assert partition.dominant_other_surah(113, rows) is None
    assert partition.dominant_other_surah(1, []) is None


# ---------------------------------------------------------------------------
# resolving
# ---------------------------------------------------------------------------


def test_detected_surahs_are_adopted_and_fixed_singles_win():
    res = resolve.resolve([_file(201, _rows())], fixed={113})
    assert sorted(res.chapters) == [109, 110, 111, 112, 114]
    assert res.adopted == [109, 110, 111, 112, 114]
    assert res.ignored == {201: [113]}
    assert res.dropped == [] and res.suspect == {}


def test_a_reupload_is_ignored_and_the_stronger_copy_kept():
    rows = _rows()
    weak = [r for r in rows if partition.row_surah(r) != 111]
    weak += [r for r in rows if partition.row_surah(r) == 111][:2]
    weak.sort(key=lambda r: r["time_from"])
    res = resolve.resolve([_file(201, weak), _file(202, rows)], fixed=set())
    assert all(len(pieces) == 1 for pieces in res.chapters.values())
    assert res.chapters[111][0].file.item == 202


def test_a_surah_uploaded_in_parts_is_stitched_in_ayah_order():
    part2 = [_row("2:142:1", "2:200:5", 1.0, 900.0)]
    part1 = [_row("2:1:1", "2:141:4", 2.0, 1000.0)]
    repeat = [_row("2:150:1", "2:160:3", 0.0, 60.0)]
    res = resolve.resolve([_file(201, part2), _file(202, part1), _file(203, repeat)], fixed=set())
    assert [p.file.item for p in res.chapters[2]] == [202, 201]
    assert res.ignored == {203: [2]}


def test_planned_chapters_missing_everywhere_are_dropped_and_empty_files_reported():
    res = resolve.resolve(
        [_file(201, _rows(), planned=(108, 109)), _file(202, [_row("", "", 0, 30)])], fixed=set()
    )
    assert res.dropped == [108]
    assert res.chapters[109][0].file.item == 201
    assert 110 in res.adopted
    assert res.empty == ["https://src/202"]


def test_excerpts_of_other_surahs_are_fragments_not_chapters():
    # A CD intro montage: short excerpts of 78 and 80, then a full al-Kawthar.
    montage = [
        _row("78:40:1", "78:40:10", 1.0, 20.0),
        _row("80:34:1", "80:36:2", 22.0, 40.0),
        _row("108:1:1", "108:3:6", 45.0, 60.0),
    ]
    res = resolve.resolve([_file(201, montage)], fixed=set(), ayah_counts={78: 40, 80: 42, 108: 3})
    assert sorted(res.chapters) == [108]
    assert sorted(res.fragments) == [78, 80]
    assert "only 1 of 40 ayahs" in res.fragments[78]
    assert res.ignored == {201: [78, 80]}


def test_a_cut_absorbing_a_missed_surah_is_suspect():
    # The aligner could not place al-Ikhlas: its recitation comes back unmatched.
    rows = [r for r in _rows() if partition.row_surah(r) != 112 and r["time_from"] < 150]
    rows += [_row("", "", 153.06, 173.16)]
    rows += [r for r in _rows() if r["time_from"] >= 176]
    res = resolve.resolve([_file(201, rows)], fixed=set())
    assert 112 not in res.chapters
    assert set(res.suspect) == {111}
    assert "chapter 112" in res.suspect[111]


# ---------------------------------------------------------------------------
# split stage (job stubbed)
# ---------------------------------------------------------------------------


@pytest.fixture
def split_env(tmp_path, monkeypatch):
    from services import hf_bucket as _hf_bucket
    from services.admin.align_pipeline import stage_acquire
    from services.audio import audio_meta
    from tests.conftest import _seed_catalog, _seed_state

    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))
    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)
    _seed_catalog(
        vocab=Vocab(
            riwayat=[Riwayah(slug="hafs", short="H", name="Hafs")],
            styles=[Style(slug="murattal", short="M", name="Murattal")],
            sources=[Source(slug="src1", name="Source One")],
            channels=[Channel(slug="ch1", short="c1", name="Channel One")],
        ),
        reciters=[ReciterEntry(reciter_id="rec_comb", name_en="Reciter")],
        deliveries=[
            Delivery(
                slug=SLUG,
                reciter_id="rec_comb",
                riwayah="hafs",
                style="murattal",
                source="src1",
                channel="ch1",
                audio_category=AudioCategory.BY_SURAH,
                chapter_count=7,
                added_at=datetime.now(UTC),
                added_by_hf_id="seed",
            ),
        ],
    )
    _seed_state(SLUG, state="awaiting_alignment", reciter_id="rec_comb")
    _write_manifest(
        backend,
        {"113": {"url": "https://cdn/113.mp3"}, "114": {"url": "https://cdn/114.mp3"}},
        sources=[{"url": DRIVE, "title": "whatever the uploader typed"}],
    )
    audio_meta._clear_for_test()
    launched: list[dict] = []

    def fake_launch(kind, entrypoint, slug, run_id, **kw):
        plan = json.loads(backend.read_bytes(f"staging/{slug}/{run_id}/split_plan.json"))
        launched.append(plan)
        return "job-1"

    def fake_wait(kind, slug, run_id, job_id, report_path):
        cuts = {
            ch: {
                "bytes": 1000,
                "duration_ms": sum(end - start for _slot, start, end in pieces),
                "pieces": len(pieces),
            }
            for ch, pieces in launched[-1]["chapters"].items()
        }
        return {"cuts": cuts, "failures": {}}

    monkeypatch.setattr(stage_acquire, "launch_job", fake_launch)
    monkeypatch.setattr(stage_acquire, "wait_job", fake_wait)
    yield backend, launched
    audio_meta._clear_for_test()
    _hf_bucket.reset_backend()


def test_split_stage_detects_surahs_and_rewrites_the_manifest(split_env):
    from services.admin.align_pipeline import sources, stage_split, staging
    from services.state import catalog as catalog_service

    backend, launched = split_env
    run_id = "run-1"
    groups = sources.groups_for(SLUG)
    detect = next(g for g in groups if g.detect)
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    staging.write_json(staging.source_path(SLUG, run_id, detect.item), doc)
    # 113's single file really holds 113; 114's file holds 113 too (mislabelled).
    only_113 = {"segments": [r for r in doc["segments"] if partition.row_surah(r) == 113]}
    staging.write_json(staging.chapter_path(SLUG, run_id, 113), only_113)
    staging.write_json(staging.chapter_path(SLUG, run_id, 114), only_113)
    staging.write_json(
        staging.acquire_path(SLUG, run_id), {"sources": {str(detect.item): {"duration_ms": 255076}}}
    )

    outcome = stage_split.run(SLUG, run_id, groups)

    assert outcome["mismatched"] == {"114": 113}
    assert outcome["dropped"] == []  # the mislabelled 114 file is replaced by the playlist file
    assert outcome["ignored"] == {str(detect.item): [113]}
    assert sorted(outcome["adopted"], key=int) == ["109", "110", "111", "112", "114"]
    assert sorted(launched[-1]["chapters"], key=int) == ["109", "110", "111", "112", "114"]
    assert launched[-1]["slots"] == [detect.item]

    manifest = json.loads(backend.read_bytes(f"catalog/audio_manifest/{SLUG}.json"))
    assert sorted(manifest["chapters"], key=int) == ["109", "110", "111", "112", "113", "114"]
    assert "sources" not in manifest  # resolved: the manifest is back to its usual shape
    entry = manifest["chapters"]["110"]
    assert entry["url"].endswith(f"/reciters/{SLUG}/audio/110.mp3")
    assert entry["source_url"] == DRIVE and entry["source_offset_ms"] > 0
    assert entry["bitrate_mode"] == "cbr" and entry["size_bytes"] == 1000
    assert manifest["_meta"]["chapter_count"] == 6

    staged = staging.read_json(staging.chapter_path(SLUG, run_id, 110))
    assert staged is not None
    assert staged["_inspector"]["split_offset_ms"] == entry["source_offset_ms"]
    assert min(r["time_from"] for r in staged["segments"]) >= 0
    delivery = catalog_service.find_delivery(SLUG)
    assert delivery is not None and delivery.chapter_count == 6
    # Idempotent on resume: the recorded outcome short-circuits a second pass.
    assert stage_split.run(SLUG, run_id, groups) == outcome
    assert len(launched) == 1


def test_split_stage_stitches_parts_onto_one_timeline(split_env):
    from services.admin.align_pipeline import sources, stage_split, staging
    from services.audio import audio_meta

    backend, launched = split_env
    _write_manifest(backend, {}, sources=[{"url": "https://yt/p1"}, {"url": "https://yt/p2"}])
    audio_meta._clear_for_test()
    run_id = "run-parts"
    groups = sources.groups_for(SLUG)
    p1 = {"segments": [_row("2:1:1", "2:141:4", 10.0, 1000.0)]}
    p2 = {"segments": [_row("2:142:1", "2:286:8", 5.0, 800.0)]}
    staging.write_json(staging.source_path(SLUG, run_id, groups[0].item), p1)
    staging.write_json(staging.source_path(SLUG, run_id, groups[1].item), p2)

    outcome = stage_split.run(SLUG, run_id, groups)

    assert outcome["stitched"] == {"2": 2}
    pieces = launched[-1]["chapters"]["2"]
    assert [p[0] for p in pieces] == [groups[0].item, groups[1].item]
    staged = staging.read_json(staging.chapter_path(SLUG, run_id, 2))
    assert staged is not None
    first_len = (pieces[0][2] - pieces[0][1]) / 1000
    second = staged["segments"][1]
    assert second["time_from"] == pytest.approx(first_len + 5.0 - pieces[1][1] / 1000)
    assert len(staged["_inspector"]["split_pieces"]) == 2
    manifest = json.loads(backend.read_bytes(f"catalog/audio_manifest/{SLUG}.json"))
    assert manifest["chapters"]["2"]["source_url"] == "https://yt/p1"


def test_a_realign_reads_already_cut_chapters_as_single_files(split_env):
    from services.admin.align_pipeline import sources
    from services.audio import audio_meta

    backend, _ = split_env
    cut_url = sources.bucket_chapter_url(SLUG, 110)
    _write_manifest(backend, {"110": {"url": cut_url, "source_url": DRIVE, "source_offset_ms": 5}})
    audio_meta._clear_for_test()
    [group] = sources.groups_for(SLUG)
    assert (group.url, group.chapters, group.slot) == (cut_url, (110,), None)


def test_runner_freezes_groups_for_the_run(split_env):
    from services.admin.align_pipeline import runner, staging

    backend, _launched = split_env
    first = runner._groups(SLUG, "run-2")
    backend.write_json_atomic(
        f"catalog/audio_manifest/{SLUG}.json",
        {
            "slug": SLUG,
            "chapters": {"1": {"url": "x"}},
            "_meta": {"checksum": "x", "chapter_count": 1, "category": "by_surah"},
        },
    )
    from services.audio import audio_meta

    audio_meta._clear_for_test()
    assert runner._groups(SLUG, "run-2") == first
    assert staging.read_json(staging.run_file(SLUG, "run-2", staging.GROUPS_FILE))


def test_record_acquired_fills_only_unprobed_fields(split_env):
    from services.admin.align_pipeline import manifest

    backend, _ = split_env
    manifest.record_acquired(SLUG, {"113": {"bytes": 777, "duration_ms": 33900}})
    doc = json.loads(backend.read_bytes(f"catalog/audio_manifest/{SLUG}.json"))
    assert doc["chapters"]["113"]["size_bytes"] == 777
    assert doc["chapters"]["113"]["duration_sec"] == 34
    assert doc["chapters"]["114"].get("size_bytes") is None


def test_a_duplicate_upload_is_a_repeat_and_its_missing_surah_a_gap():
    # A playlist whose "046" file is a copy of "047": both hold surah 47, so
    # surah 46 is missing inside the delivery's 45..48 span.
    files = [
        _file(245, [_row("45:1:1", "45:37:8", 1.0, 600.0)]),
        _file(246, [_row("47:1:1", "47:38:29", 1.0, 700.0)]),
        _file(247, [_row("47:1:1", "47:38:29", 1.0, 699.0)]),
        _file(248, [_row("48:1:1", "48:29:54", 1.0, 750.0)]),
    ]
    res = resolve.resolve(files, fixed=set())
    assert sorted(res.chapters) == [45, 47, 48]
    assert res.repeats == {"https://src/247": {47: "https://src/246"}}
    assert res.gaps == [46]


def test_coverage_report_lists_gaps_as_missing_and_repeats_as_unresolved(tmp_path):
    from services.admin.align_pipeline import stage_assemble

    stage_assemble._write_coverage(
        tmp_path,
        [45, 47, 48],
        {"dropped": [], "gaps": [46], "repeats": {"https://src/247": {"47": "https://src/246"}}},
    )
    report = json.loads((tmp_path / "coverage_report.json").read_text(encoding="utf-8"))
    assert report["missing"] == [46] and report["clean"] is False
    assert report["unresolved_files"] == [
        "https://src/247: repeats surah 47, already taken from https://src/246"
    ]
