"""Combined source files in the align pipeline — grouping, partition, split stage.

The partition is exercised against a real aligner result (one file holding
surahs 109–114, ``tests/fixtures/align/combined_109_114.json``); the split HF
Job is stubbed with a fake report so the stage's bookkeeping (staged chapters,
manifest offsets, dropped/adopted chapters) runs against an in-memory bucket.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qua_shared.audio.sources import groups_from_manifest, needs_ytdlp
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
from services.admin.align_pipeline import partition

FIXTURE = Path(__file__).parents[1] / "fixtures" / "align" / "combined_109_114.json"
SLUG = "rec_comb"
DRIVE = "https://drive.google.com/file/d/1rl6qU2TnCacbR_VjK5V-wFm97Zn-cHSg/view"


def _rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["segments"]


# ---------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------


def test_groups_split_single_and_combined_sources():
    groups = groups_from_manifest(
        {
            "3": {"url": "https://cdn/3.mp3"},
            "1": {"url": "u1", "source_url": DRIVE},
            "2": {"url": "u2", "source_url": DRIVE},
            "78": {"url": "u78", "source_url": "https://www.youtube.com/watch?v=a"},
            "79": {"url": "u79", "source_url": "https://www.youtube.com/watch?v=a"},
        }
    )
    assert [(g.chapters, g.slot) for g in groups] == [((1, 2), 901), ((3,), None), ((78, 79), 902)]
    assert [g.item for g in groups] == [901, 3, 902]


def test_groups_refuse_a_non_consecutive_combined_file():
    with pytest.raises(ValueError, match="non-consecutive"):
        groups_from_manifest(
            {"1": {"url": "a", "source_url": "s"}, "3": {"url": "b", "source_url": "s"}}
        )


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
# partition (real aligner output)
# ---------------------------------------------------------------------------


def test_partition_cuts_every_surah_without_overlap():
    part = partition.partition(
        _rows(), planned=(109, 110, 111, 112, 113, 114), claimed_elsewhere=set(), duration_ms=255076
    )
    assert sorted(part.cuts) == [109, 110, 111, 112, 113, 114]
    assert part.missing == [] and part.ignored == []
    cuts = [part.cuts[c] for c in sorted(part.cuts)]
    for a, b in zip(cuts, cuts[1:], strict=False):
        assert a.end_ms <= b.start_ms
    # Each chapter opens on its own Basmala: specials attach forward.
    for cut in cuts:
        assert cut.rows[0]["kind"] == "special"
        assert all(partition.row_surah(r) in (None, cut.chapter) for r in cut.rows)
    # 109 begins after the 4 s lead-in silence, padded by TRIM_PAD_MS.
    assert part.cuts[109].start_ms == round(4.46 * 1000) - partition.TRIM_PAD_MS
    assert part.cuts[114].end_ms <= 255076


def test_partition_reports_missing_ignored_and_adopted():
    part = partition.partition(
        _rows(),
        planned=(108, 109, 110),
        claimed_elsewhere={111, 112},
        duration_ms=None,
    )
    assert part.missing == [108]
    assert part.ignored == [111, 112]
    # 113/114 were planned nowhere: adopted into this file's cuts.
    assert sorted(part.cuts) == [109, 110, 113, 114]


def test_rebase_moves_rows_onto_the_cut_timeline():
    cut = partition.partition(
        _rows(), planned=(110,), claimed_elsewhere={109, 111, 112, 113, 114}, duration_ms=None
    ).cuts[110]
    rebased = partition.rebase(cut.rows, cut.start_ms)
    assert rebased[0]["time_from"] == pytest.approx(cut.rows[0]["time_from"] - cut.start_ms / 1000)
    assert min(r["time_from"] for r in rebased) >= 0


def test_unmatched_ms_sums_only_unplaced_recitation():
    rows = [
        {"kind": "special", "ref_from": "", "time_from": 0.0, "time_to": 5.0},
        {"kind": "quran", "ref_from": "", "time_from": 5.5, "time_to": 26.0},
        {"kind": "quran", "ref_from": "113:3:1", "time_from": 26.0, "time_to": 40.0},
    ]
    assert partition.unmatched_ms(rows) == 20500


def test_a_cut_holding_a_missed_chapters_audio_is_flagged():
    from services.admin.align_pipeline import stage_split

    loose = {"kind": "quran", "ref_from": "", "time_from": 5.5, "time_to": 26.0}
    part = partition.Partition(
        cuts={113: partition.ChapterCut(113, 0, 40380, rows=[loose])}, missing=[112], ignored=[]
    )
    outcome: dict = {}
    stage_split._flag_suspects(part, outcome)
    assert outcome == {
        "suspect": {"113": "holds 20s of unmatched audio; chapter(s) 112 expected in the same file"}
    }
    part.missing = []
    outcome = {}
    stage_split._flag_suspects(part, outcome)
    assert outcome == {}


def test_dominant_other_surah_flags_a_mislabelled_single_file():
    rows = [r for r in _rows() if partition.row_surah(r) == 113]
    assert partition.dominant_other_surah(94, rows) == 113
    assert partition.dominant_other_surah(113, rows) is None
    assert partition.dominant_other_surah(1, []) is None


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
    chapters = {str(c): {"url": f"https://b/{c}.mp3", "source_url": DRIVE} for c in range(108, 113)}
    chapters["113"] = {"url": "https://cdn/113.mp3"}
    chapters["114"] = {"url": "https://cdn/114.mp3"}
    backend.write_json_atomic(
        f"catalog/audio_manifest/{SLUG}.json",
        {
            "slug": SLUG,
            "chapters": chapters,
            "_meta": {"checksum": "x", "chapter_count": 7, "category": "by_surah"},
        },
    )
    audio_meta._clear_for_test()
    launched: list[dict] = []

    def fake_launch(kind, entrypoint, slug, run_id, **kw):
        plan = json.loads(backend.read_bytes(f"staging/{slug}/{run_id}/split_plan.json"))
        launched.append(plan)
        return "job-1"

    def fake_wait(kind, slug, run_id, job_id, report_path):
        plan = launched[-1]["slots"]
        cuts = {
            ch: {"slot": int(slot), "offset_ms": w[0], "bytes": 1000, "duration_ms": w[1] - w[0]}
            for slot, spec in plan.items()
            for ch, w in spec["chapters"].items()
        }
        return {"cuts": cuts, "failures": {}}

    monkeypatch.setattr(stage_acquire, "launch_job", fake_launch)
    monkeypatch.setattr(stage_acquire, "wait_job", fake_wait)
    yield backend, launched
    audio_meta._clear_for_test()
    _hf_bucket.reset_backend()


def test_split_stage_stages_rebased_chapters_and_rewrites_the_manifest(split_env):
    from services.admin.align_pipeline import sources, stage_split, staging

    backend, launched = split_env
    run_id = "run-1"
    groups = sources.groups_for(SLUG)
    combined = next(g for g in groups if g.combined)
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    staging.write_json(staging.source_path(SLUG, run_id, combined.item), doc)
    # 113's single file really holds 113; 114's file holds 113 too (mislabelled).
    only_113 = {"segments": [r for r in doc["segments"] if partition.row_surah(r) == 113]}
    staging.write_json(staging.chapter_path(SLUG, run_id, 113), only_113)
    staging.write_json(staging.chapter_path(SLUG, run_id, 114), only_113)
    staging.write_json(
        staging.acquire_path(SLUG, run_id), {"sources": {"901": {"duration_ms": 255076}}}
    )

    outcome = stage_split.run(SLUG, run_id, groups)

    assert outcome["mismatched"] == {"114": 113}
    assert sorted(outcome["dropped"]) == [108, 114]  # 108 not in the file; 114 mislabelled
    assert outcome["ignored"] == {"901": [113]}  # the combined file's 113 belongs to 113's file
    # 114's own file was wrong, so the combined file's 114 is adopted instead.
    assert outcome["adopted"] == {"114": DRIVE}
    assert sorted(launched[-1]["slots"]["901"]["chapters"], key=int) == [
        "109",
        "110",
        "111",
        "112",
        "114",
    ]

    manifest = json.loads(backend.read_bytes(f"catalog/audio_manifest/{SLUG}.json"))
    assert sorted(manifest["chapters"], key=int) == ["109", "110", "111", "112", "113", "114"]
    assert manifest["chapters"]["114"]["source_url"] == DRIVE
    entry = manifest["chapters"]["110"]
    assert entry["source_url"] == DRIVE and entry["source_offset_ms"] > 0
    assert entry["bitrate_mode"] == "cbr" and entry["size_bytes"] == 1000
    assert manifest["_meta"]["chapter_count"] == 6

    staged = staging.read_json(staging.chapter_path(SLUG, run_id, 110))
    assert staged is not None
    assert staged["_inspector"]["split_offset_ms"] == entry["source_offset_ms"]
    assert min(r["time_from"] for r in staged["segments"]) >= 0

    from services.state import catalog as catalog_service

    delivery = catalog_service.find_delivery(SLUG)
    assert delivery is not None and delivery.chapter_count == 6
    # Idempotent on resume: the recorded outcome short-circuits a second pass.
    assert stage_split.run(SLUG, run_id, groups) == outcome
    assert len(launched) == 1


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
    assert (
        "size_bytes" not in doc["chapters"]["114"] or doc["chapters"]["114"]["size_bytes"] is None
    )
