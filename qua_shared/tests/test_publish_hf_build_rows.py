"""build_rows: segment word-range (from matched_ref) + source_url (from manifest).

Regression guards for two corruptions: segments collapsing to ``[1,1,...]`` (no
word_from/word_to in detailed.json — the span lives in ``matched_ref``) and an
empty ``source_url`` (detailed.json has no per-entry ``audio`` field; the URL
comes from the audio manifest's chapter map).
"""

from __future__ import annotations

import sys
import types

import pytest

from qua_jobs import publish_hf
from qua_jobs.publish_hf import (
    _detailed_by_ref,
    _rebase_row_multi,
    _seg_word_range,
    _subtract_spans,
    _verses_for_validation,
    build_rows,
)
from qua_shared.dataset_validation import fatal_violations, validate_dataset

_SURAH_INFO = {"1": {"verses": [{"verse": 1, "num_words": 4}, {"verse": 2, "num_words": 3}]}}


def test_hf_push_preserves_riwayah_directory_and_slug_filename(monkeypatch):
    pushed = {}

    class FakeDataset:
        @classmethod
        def from_generator(cls, generator, features, gen_kwargs, **kwargs):
            pushed["data"] = list(generator(**gen_kwargs))
            pushed["features"] = features
            pushed["from_generator"] = kwargs
            return cls()

        def __len__(self):
            return len(pushed["data"])

        def push_to_hub(self, repo_id, **kwargs):
            pushed.update(repo_id=repo_id, **kwargs)

    datasets = types.SimpleNamespace(
        Audio=lambda: object(),
        Dataset=FakeDataset,
        Features=lambda value: value,
        Sequence=lambda value: value,
        Value=lambda value: value,
    )
    api = types.SimpleNamespace(
        create_repo=lambda **kwargs: None,
        repo_info=lambda **kwargs: types.SimpleNamespace(sha="revision"),
    )
    hub = types.SimpleNamespace(HfApi=lambda token=None: api)
    monkeypatch.setitem(sys.modules, "datasets", datasets)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(publish_hf, "_resolve_dataset_repo_id", lambda: "owner/dataset")
    row = {
        "surah": 1,
        "ayah": 1,
        "duration_ms": 10,
        "text_uthmani": "x",
        "segments": [],
        "word_timestamps": [],
        "source_url": "https://example.test/1.mp3",
        "clip_start": 0,
    }

    assert publish_hf._push_to_hf("reciter_slug", "hafs_an_asim", [row], [b"mp3"]) == "revision"
    assert pushed["config_name"] == "hafs_an_asim"
    assert pushed["split"] == "reciter_slug"
    assert "letter_timestamps" not in pushed["data"][0]
    assert "letter_timestamps" not in pushed["features"]
    assert pushed["from_generator"]["writer_batch_size"] == 64
    assert pushed["max_shard_size"] == "500MB"


def test_seg_word_range_single_ayah():
    assert _seg_word_range("1:1:1-1:1:4", "1", 1, _SURAH_INFO) == (1, 4)
    assert _seg_word_range("1:2:2-1:2:3", "1", 2, _SURAH_INFO) == (2, 3)


def test_seg_word_range_cross_verse_clips_to_ayah():
    # Segment spans 1:1 word 3 -> 1:2 word 2.
    assert _seg_word_range("1:1:3-1:2:2", "1", 1, _SURAH_INFO) == (3, 4)  # to end of 1:1
    assert _seg_word_range("1:1:3-1:2:2", "1", 2, _SURAH_INFO) == (1, 2)  # from start of 1:2


def test_seg_word_range_non_overlapping_is_none():
    assert _seg_word_range("1:2:1-1:2:3", "1", 1, _SURAH_INFO) is None
    assert _seg_word_range("", "1", 1, _SURAH_INFO) is None


def test_build_rows_segments_and_source_url():
    detailed = {
        "entries": [
            {
                "ref": "1",
                "segments": [
                    {"time_start": 0, "time_end": 4000, "matched_ref": "1:1:1-1:1:4"},
                ],
            }
        ]
    }
    timestamps = {
        "1:1": {
            "words": [[1, 0, 1000], [2, 1000, 2000], [3, 2000, 3000], [4, 3000, 4000]],
            "word_texts": ["a", "b", "c", "d"],
            "tokens_by_word": [
                [
                    {
                        "start_ms": 100,
                        "end_ms": 200,
                        "owns_sound": False,
                        "paint": [[0, 1]],
                    }
                ],
                [],
                [],
                [],
            ],
            "verse_start_ms": 0,
            "verse_end_ms": 4000,
        }
    }
    rows = build_rows(
        timestamps,
        _detailed_by_ref(detailed),
        _SURAH_INFO,
        {"1": "https://cdn.example/001.mp3"},
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source_url"] == "https://cdn.example/001.mp3"
    # Word span 1->4 (basmala), NOT the collapsed [1, 1].
    assert row["segments"][0][0] == 1
    assert row["segments"][0][1] == 4
    assert row["text_uthmani"] == "a b c d"
    assert "letter_timestamps" not in row


# ---------------------------------------------------------------------------
# Interior no-match audio excision: keep_runs + piecewise rebase + full drop.
# ---------------------------------------------------------------------------


def test_subtract_spans_no_gap_is_single_run():
    assert _subtract_spans(0, 4000, []) == [(0, 4000)]
    # A span outside the window is ignored.
    assert _subtract_spans(0, 4000, [(5000, 6000)]) == [(0, 4000)]


def test_subtract_spans_interior_and_overlapping():
    assert _subtract_spans(0, 4000, [(2000, 3000)]) == [(0, 2000), (3000, 4000)]
    # Overlapping / out-of-order spans are clipped + merged.
    assert _subtract_spans(0, 4000, [(2500, 3000), (2000, 2600)]) == [(0, 2000), (3000, 4000)]
    # Span clipped to the window edges.
    assert _subtract_spans(0, 4000, [(-100, 500)]) == [(500, 4000)]


def _ts(words, start, end):
    return {
        "words": words,
        "word_texts": ["x"] * len(words),
        "tokens_by_word": [[] for _ in words],
        "verse_start_ms": start,
        "verse_end_ms": end,
    }


def test_build_rows_keep_runs_contiguous_single_run():
    # No no-match segment → one run spanning the whole verse (today's behavior).
    detailed = {
        "entries": [
            {
                "ref": "1:1",
                "segments": [{"time_start": 0, "time_end": 4000, "matched_ref": "1:1:1-1:1:4"}],
            }
        ]
    }
    timestamps = {
        "1:1": _ts([[1, 0, 1000], [2, 1000, 2000], [3, 2000, 3000], [4, 3000, 4000]], 0, 4000)
    }
    rows = build_rows(timestamps, _detailed_by_ref(detailed), _SURAH_INFO, {"1": "u"})
    # One run; the chapter-last verse takes the default pad_end (300ms) tail
    # headroom, so the run ends at verse_end + 300.
    assert rows[0]["keep_runs"] == [(0, 4300)]


def test_build_rows_keep_runs_interior_no_match_splits():
    # A no-match middle segment ([2000,3000], empty matched_ref) splits the clip
    # into two kept runs so the slicer can excise the gap audio.
    detailed = {
        "entries": [
            {
                "ref": "1:1",
                "segments": [
                    {"time_start": 0, "time_end": 2000, "matched_ref": "1:1:1-1:1:2"},
                    {"time_start": 2000, "time_end": 3000, "matched_ref": ""},  # no-match
                    {"time_start": 3000, "time_end": 4000, "matched_ref": "1:1:3-1:1:4"},
                ],
            }
        ]
    }
    timestamps = {
        "1:1": _ts([[1, 0, 1000], [2, 1000, 2000], [3, 3000, 3500], [4, 3500, 4000]], 0, 4000)
    }
    rows = build_rows(timestamps, _detailed_by_ref(detailed), _SURAH_INFO, {"1": "u"})
    assert len(rows) == 1
    # Interior no-match [2000,3000] excised; chapter-last verse takes the default
    # pad_end (300ms) tail, so the trailing run ends at verse_end + 300.
    assert rows[0]["keep_runs"] == [(0, 2000), (3000, 4300)]


def test_build_rows_repeated_pivot_segments_no_overlap():
    # 2:6 shape: word 6 is the look-back pivot recited in both segments, so the
    # flat words carry word 6 twice. With the projection's occurrence spans,
    # build_rows must pin each segment to its own occurrence — segments stay
    # non-overlapping and the intra-segment-gap validator passes. (An index-keyed
    # map would tie both segment-6 boundaries to the last occurrence, overlapping
    # them and tripping a phantom gap.)
    surah_info = {"1": {"verses": [{"verse": 1, "num_words": 11}]}}
    detailed = {
        "entries": [
            {
                "ref": "1:1",
                "segments": [
                    {"time_start": 0, "time_end": 5000, "matched_ref": "1:1:1-1:1:6"},
                    {"time_start": 5010, "time_end": 9000, "matched_ref": "1:1:6-1:1:11"},
                ],
            }
        ]
    }
    words = [
        [1, 100, 1000],
        [2, 1000, 1900],
        [3, 1900, 2700],
        [4, 2700, 3500],
        [5, 3500, 4300],
        [6, 4300, 5000],  # word 6, first occurrence
        [6, 5010, 5800],
        [7, 5800, 6400],
        [8, 6400, 7000],
        [9, 7000, 7600],
        [10, 7600, 8200],
        [11, 8200, 9000],  # word 6, second occurrence
    ]
    timestamps = {
        "1:1": {
            "words": words,
            "word_texts": ["x"] * len(words),
            "tokens_by_word": [[] for _ in words],
            "verse_start_ms": 100,
            "verse_end_ms": 9000,
            "seg_spans": [
                {
                    "ref": "1:1",
                    "w_from": 1,
                    "w_to": 6,
                    "occ_start": 0,
                    "occ_end": 6,
                    "start_ms": 100,
                    "end_ms": 5000,
                },
                {
                    "ref": "1:1",
                    "w_from": 6,
                    "w_to": 11,
                    "occ_start": 6,
                    "occ_end": 12,
                    "start_ms": 5010,
                    "end_ms": 9000,
                },
            ],
        }
    }
    rows = build_rows(timestamps, _detailed_by_ref(detailed), surah_info, {"1": "u"})
    assert len(rows) == 1
    segs = rows[0]["segments"]
    assert len(segs) == 2
    # Internal boundary: seg A end <= seg B start (non-overlapping; the 10ms is the
    # recovered silence between the two passes of word 6).
    assert segs[0][3] <= segs[1][2]
    # The validator (intra-segment gaplessness) must be clean.
    summary = validate_dataset(_verses_for_validation(rows), surah_info=surah_info)
    assert fatal_violations(summary["violations"]) == []


def test_build_rows_full_verse_no_match_drops_row():
    # 1:2's only segment is no-match → the projection never emits 1:2, so it's
    # absent from timestamps → no row built (point 2: full-verse drop).
    detailed = {
        "entries": [
            {
                "ref": "1:1",
                "segments": [{"time_start": 0, "time_end": 4000, "matched_ref": "1:1:1-1:1:4"}],
            },
            {"ref": "1:2", "segments": [{"time_start": 4000, "time_end": 6000, "matched_ref": ""}]},
        ]
    }
    timestamps = {
        "1:1": _ts([[1, 0, 1000], [2, 1000, 2000], [3, 2000, 3000], [4, 3000, 4000]], 0, 4000)
    }
    rows = build_rows(timestamps, _detailed_by_ref(detailed), _SURAH_INFO, {"1": "u"})
    assert [(r["surah"], r["ayah"]) for r in rows] == [(1, 1)]


def test_rebase_row_multi_excises_gap_gaplessly():
    from qua_shared.mp3_frames import RunMap

    # Verse clip [0,4000] source; word 3 has a 1000 ms phantom gap before it
    # (the excised no-match audio at [2000,3000]).
    row = {
        "clip_start": 0,
        "clip_end": 4000,
        "duration_ms": 4000,
        "word_timestamps": [[1, 0, 1000], [2, 1000, 2000], [3, 3000, 3500], [4, 3500, 4000]],
        "segments": [[1, 2, 0, 2000], [3, 4, 3000, 4000]],
    }
    # Runs: [0,2000) then [3000,4000) placed right after (cum_offset 2000).
    runs = [RunMap(0, 2000, 0), RunMap(3000, 4000, 2000)]
    _rebase_row_multi(row, runs)
    assert row["duration_ms"] == 3000
    assert row["clip_start"] == 0
    assert row["clip_end"] == 3000
    # Word 3 now starts exactly at word 2's end (2000) — the gap is gone.
    assert row["word_timestamps"] == [
        [1, 0, 1000],
        [2, 1000, 2000],
        [3, 2000, 2500],
        [4, 2500, 3000],
    ]
    assert row["segments"] == [[1, 2, 0, 2000], [3, 4, 2000, 3000]]


def test_the_hf_config_name_stays_the_inspector_slug():
    """The config name IS the dataset's parquet folder.

    Every split published so far lives under ``hafs_an_asim/``. Pushing the SDK
    slug would open a second ``hafs/`` folder, duplicate the config in the card
    frontmatter and orphan the existing split.
    """
    from qua_jobs import publish_hf

    # No riwayah anywhere — every pre-multi-riwayah reciter.
    assert publish_hf._config_riwayah(None, {}) == ("hafs", "hafs_an_asim")
    # A legacy row that stored the short form lands on the SAME folder.
    assert publish_hf._config_riwayah({"_meta": {"riwayah": "hafs"}}, {}) == (
        "hafs",
        "hafs_an_asim",
    )
    assert publish_hf._config_riwayah({"_meta": {"riwayah": "warsh_an_nafi"}}, {}) == (
        "warsh",
        "warsh_an_nafi",
    )


def test_an_unknown_riwayah_is_refused_rather_than_given_a_folder():
    from qua_jobs import publish_hf
    from qua_shared.riwayat import UnsupportedRiwayah

    with pytest.raises(UnsupportedRiwayah):
        publish_hf._config_riwayah({"_meta": {"riwayah": "duri_an_abi_amr"}}, {})


def test_manifest_chapter_sources_resolve_the_public_link_and_offset():
    manifest = {
        "chapters": {
            "2": {"url": "/srv/scratch/x/002.mp3", "source_url": "https://youtu.be/AAA"},
            "3": {"url": "https://cdn.example/3.mp3", "source_offset_ms": 1200},
        }
    }

    urls, offsets = publish_hf._manifest_chapter_sources(manifest)

    assert urls == {"2": "https://youtu.be/AAA", "3": "https://cdn.example/3.mp3"}
    assert offsets == {"2": 0, "3": 1200}


def test_manifest_chapter_sources_refuse_a_local_path():
    # Issue #285: an offline-intake manifest carried a scratch path as ``url``;
    # the publish must stop rather than ship it (or an empty link) as source_url.
    manifest = {"chapters": {"1": {"url": "/srv/scratch/qua_offline/intake/audio/x/001.mp3"}}}

    with pytest.raises(RuntimeError, match=r"\['1'\]"):
        publish_hf._manifest_chapter_sources(manifest)
