from __future__ import annotations

import gzip
import json
import sys
import types

import brotli
import orjson
import pytest
from pydantic import ValidationError

from qua_jobs import cut_release, publish_hf
from qua_shared.dataset_validation import fatal_violations, validate_dataset
from qua_shared.timestamps_codec import decode_document
from qua_shared.timestamps_native import (
    project_native_shard,
    project_shard,
    project_shard_occurrences,
    project_word_shard,
    select_complete_verses,
)
from qua_shared.timestamps_shards import (
    brotli_shard,
    build_timestamp_shards,
    validated_brotli_shard,
    write_validated_shard,
)
from qua_shared.timestamps_v13_audit import V13AuditError
from qua_shared.verse_layout import build_verse_layouts, load_canonical_verses, reshape_canonical


def _reading(reading_id: str, specs: list[tuple[str, tuple[int, int], list[int]]]) -> dict:
    words, word_timing, animation, animation_timing, parts, boundaries = [], [], [], [], [], []
    for ref, span, indexes in specs:
        first = len(words)
        width = max(1, (span[1] - span[0]) // len(indexes))
        for offset, index in enumerate(indexes):
            word_id = len(words)
            start = span[0] + offset * width
            end = start + width
            words.append([f"{ref}:{index}", "x", [], [], [], [], []])
            boundaries.append([1, [], [], [], None, None])
            word_timing.append([start, end])
            animation.append(
                [word_id, [100 + word_id], [200 + word_id], [200 + word_id], "x", [], 0, None]
            )
            animation_timing.append([start, end])
        parts.append([ref, span[0], span[1], first, len(indexes)])
    return {
        "id": reading_id,
        "parts": parts,
        "render": {
            "v": 1,
            "m": ["test", "canon", "native"],
            "p": [],
            "r": [],
            "w": words,
            "b": boundaries,
            "a": animation,
        },
        "timing": {"w": word_timing, "s": [], "a": animation_timing, "c": []},
    }


def _shard(readings: list[dict]) -> dict:
    return {
        "_meta": {
            "schema_version": 13,
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
        "readings": readings,
    }


def test_native_projection_keeps_loopback_and_earliest_complete_occasion():
    shard = _shard(
        [
            _reading("r1", [("1:1", (0, 400), [1, 2]), ("1:1", (400, 900), [2, 3])]),
            _reading("r2", [("1:2", (900, 1100), [1])]),
            _reading("r3", [("1:1", (1100, 1600), [1, 2, 3])]),
        ]
    )
    projected = project_native_shard(shard)
    assert [word["index"] for word in projected["1:1"]["words"]] == [1, 2, 2, 3]
    assert projected["1:1"]["verse_start_ms"] == 0
    assert projected["1:1"]["verse_end_ms"] == 900


def test_occurrence_projection_emits_every_take_and_flags_the_canonical_one():
    """Same shard as the canonical test: r1 (a lookback take of 1:1) is the
    canonical row, r3's re-recitation of 1:1 after 1:2 is its own unflagged
    row, and the timeline is audio-ordered with 1:2 in between."""
    shard = _shard(
        [
            _reading("r1", [("1:1", (0, 400), [1, 2]), ("1:1", (400, 900), [2, 3])]),
            _reading("r2", [("1:2", (900, 1100), [1])]),
            _reading("r3", [("1:1", (1100, 1600), [1, 2, 3])]),
        ]
    )
    rows = project_shard_occurrences(shard)
    assert [(row["ref"], row["canonical"]) for row in rows] == [
        ("1:1", True),
        ("1:2", True),
        ("1:1", False),
    ]
    assert [row["verse_start_ms"] for row in rows] == [0, 900, 1100]
    assert [word["index"] for word in rows[2]["words"]] == [1, 2, 3]
    canonical = project_native_shard(shard)
    assert {row["ref"]: row for row in rows if row["canonical"]} == {
        ref: {"ref": ref, "canonical": True, **body} for ref, body in canonical.items()
    }


def test_occurrence_projection_splits_a_leading_false_start_into_its_own_row():
    """A restart at word 1 inside one occasion: the canonical take is the run
    from the restart; the abandoned prefix becomes an unflagged row."""
    shard = _shard(
        [
            _reading("r1", [("1:1", (0, 200), [1]), ("1:1", (200, 800), [1, 2, 3])]),
        ]
    )
    rows = project_shard_occurrences(shard)
    assert [(row["ref"], row["canonical"], row["verse_start_ms"]) for row in rows] == [
        ("1:1", False, 0),
        ("1:1", True, 200),
    ]
    assert [word["index"] for word in rows[0]["words"]] == [1]
    assert [word["index"] for word in rows[1]["words"]] == [1, 2, 3]


@pytest.mark.parametrize("following_ref", ["1:1", "1:2"])
def test_decoder_preserves_inter_reading_word_gap(
    following_ref: str,
):
    left = _reading("r1", [("1:1", (100, 200), [1])])
    right = _reading("r2", [(following_ref, (220, 300), [2])])
    left["timing"]["w"][0] = [100, 250]
    right["timing"]["w"][0] = [270, 300]

    decoded = decode_document(_shard([left, right]))

    boundary = decoded["readings"][0]["timing"]["boundaries"][-1]
    assert boundary == {
        "boundary_id": 1,
        "start_ms": 250,
        "end_ms": 270,
        "state": "join",
    }


def test_decoder_derives_internal_and_chapter_edge_boundaries():
    reading = _reading("r1", [("1:1", (100, 400), [1, 2])])
    reading["timing"]["w"] = [[120, 200], [230, 350]]

    decoded = decode_document(_shard([reading]))

    assert decoded["readings"][0]["timing"]["boundaries"] == [
        {"boundary_id": 0, "start_ms": 100, "end_ms": 120},
        {"boundary_id": 1, "start_ms": 200, "end_ms": 230, "state": "join"},
        {"boundary_id": 2, "start_ms": 350, "end_ms": 400, "state": "join"},
    ]


def test_decoder_uses_word_timings_across_verse_parts_in_one_reading():
    reading = _reading(
        "r1",
        [("1:1", (100, 200), [1]), ("1:2", (220, 400), [1])],
    )
    reading["timing"]["w"] = [[120, 180], [250, 350]]

    decoded = decode_document(_shard([reading]))

    boundary = decoded["readings"][0]["timing"]["boundaries"][1]
    assert boundary["start_ms"] == 180
    assert boundary["end_ms"] == 250


def test_native_projection_rejects_every_old_schema():
    shard = _shard([])
    shard["_meta"]["schema_version"] = 11
    with pytest.raises(ValueError, match="version 13"):
        project_native_shard(shard)


def test_complete_verse_gate_uses_native_word_indexes():
    projected = project_native_shard(_shard([_reading("r1", [("1:1", (0, 200), [1, 3])])]))
    kept, dropped = select_complete_verses(projected, {(1, 1): 3})
    assert kept == {}
    assert dropped == ["1:1"]


def _word_shard(readings: list[dict]) -> dict:
    return {"_meta": {"schema_version": 14, "profile": "word", "chapter": 19}, "readings": readings}


def _word_reading(reading_id: str, parts: list[tuple[str, int, int, list[tuple[int, int, int]]]]):
    """``parts``: ``(verse, t0, t1, [(word_index, start, end), ...])``."""
    rows, out_parts = [], []
    for verse, t0, t1, words in parts:
        out_parts.append([verse, t0, t1, len(rows), len(words)])
        rows.extend([f"{verse}:{index}", "x", start, end] for index, start, end in words)
    return {
        "id": reading_id,
        "parts": out_parts,
        "words": rows,
        "boundaries": [[1, None]] * len(rows),
    }


@pytest.mark.parametrize("version", [13, 14, 15])
@pytest.mark.parametrize("state", [0, 1, 2, 3])
@pytest.mark.parametrize("adapter", ["hf", "gh"])
def test_sakt_gap_reaches_both_release_validators(version, state, adapter, tmp_path):
    """A real shard pause survives projection, layout, and adapter validation.

    Non-sakt states keep the same gap fatal; the fixture uses an arbitrary ref
    so the permission must come from the shard, never a list of sakt verses.
    """
    if version == 14:
        reading = _word_reading(
            "r1", [("1:1", 100, 1000, [(1, 100, 200), (2, 200, 300), (3, 710, 1000)])]
        )
        reading["boundaries"][1] = [state, None]
        canonical = project_shard(_word_shard([reading]))
    else:
        reading = _reading("r1", [("1:1", (100, 1000), [1, 2, 3])])
        reading["render"]["b"][1][0] = state
        reading["timing"]["w"] = [[100, 200], [200, 300], [710, 1000]]
        reading["timing"]["a"] = [[100, 200], [200, 300], [710, 1000]]
        shard = _shard([reading])
        shard["_meta"]["schema_version"] = version
        (tmp_path / "1.json.br").write_bytes(brotli_shard(shard))
        canonical = load_canonical_verses(tmp_path)

    assert [w["sakt_after"] for w in canonical["1:1"]["words"]] == [False, state == 2, False]
    timestamps = reshape_canonical(canonical, {f"1:1:{i}": {"text": "x"} for i in (1, 2, 3)})
    if adapter == "hf":
        rows = publish_hf.build_rows(
            timestamps,
            {"1:1": {"segments": [{"matched_ref": "1:1:1-1:1:3"}]}},
            {"1": {"verses": [{"verse": 1, "num_words": 3}]}},
            pad_start=50,
        )
        row = rows[0]
        assert row["keep_runs"] == [(50, 1300)]  # sakt audio is kept, not excised
        assert row["word_timestamps"] == [[1, 50, 150], [2, 150, 250], [3, 660, 950]]
        # MP3 frame snapping shifts both sides equally and preserves the pause.
        publish_hf._rebase_row(row, 26)
        record = next(publish_hf._iter_hf_records(rows, [b"mp3"]))
        assert "sakt_after" not in record  # validation-only, no public schema change
        assert record["word_timestamps"][2][1] - record["word_timestamps"][1][2] == 410
        summary = validate_dataset(publish_hf._verses_for_validation(rows))
    else:
        layout = build_verse_layouts(timestamps)["1:1"]
        summary = validate_dataset({"1:1": cut_release._verse_for_validate(layout)})
        assert layout["words"] == [[1, 100, 200], [2, 200, 300], [3, 710, 1000]]
        occurrences = [{"ref": "1:1", "canonical": True, "layout": layout}]
        if state == 2:
            assert cut_release._validate_occurrences("slug", occurrences, {(1, 1): 3}, [])["ok"]
            files = cut_release._build_tier_files(
                "slug",
                occurrences,
                delivery_meta={"audio_category": "by_surah"},
                script_id="test",
                script_sha256="0" * 64,
                with_letters=version != 14,
            )
            word_doc = json.loads(gzip.decompress(files["word_timestamps.json.gz"]))
            assert word_doc["rows"][0][5] == layout["words"]
        else:
            with pytest.raises(cut_release._FatalViolations):
                cut_release._validate_occurrences("slug", occurrences, {(1, 1): 3}, [])
        if version != 14:
            assert [(token[1], token[2]) for token in layout["tokens"]] == [
                (100, 200),
                (200, 300),
                (710, 1000),
            ]
            assert [token[4] for token in layout["tokens"]] == [[[0, 1]], [[2, 3]], [[4, 5]]]

    assert summary["ok"] == (state == 2)
    assert [v["gap_ms"] for v in fatal_violations(summary["violations"])] == (
        [] if state == 2 else [410]
    )


@pytest.mark.parametrize("version", [13, 14, 15])
def test_sakt_projection_keeps_repeated_word_occurrences_distinct(version):
    if version == 14:
        reading = _word_reading(
            "r1", [("1:1", 0, 1000, [(1, 0, 100), (2, 100, 200), (2, 610, 700), (3, 700, 1000)])]
        )
        reading["boundaries"][1] = [2, None]
        shard = _word_shard([reading])
    else:
        reading = _reading("r1", [("1:1", (0, 1000), [1, 2, 2, 3])])
        reading["render"]["b"][1][0] = 2
        shard = _shard([reading])
        shard["_meta"]["schema_version"] = version
    canonical = project_shard(shard)
    occurrences = project_shard_occurrences(shard)
    assert [w["index"] for w in canonical["1:1"]["words"]] == [1, 2, 2, 3]
    assert [w["sakt_after"] for w in canonical["1:1"]["words"]] == [False, True, False, False]
    assert occurrences[0]["words"] == canonical["1:1"]["words"]


def test_occasion_split_reads_word_starts_not_a_cross_verse_parts_span():
    """Warsh 19:42 on the bucket: one segment 19:40:1-19:42:11 then a retake
    19:42:4-15. The word profile gave the 19:42 part its SEGMENT's start while
    the 19:41 part started later, so 19:41 looked like it began between the two
    takes of 19:42 and the verse was split into two incomplete occasions and
    gated as "missing words". Word times place the takes correctly."""
    take_one = _word_reading(
        "r55",
        [
            ("19:40", 0, 1000, [(1, 0, 500), (2, 500, 1000)]),
            ("19:41", 1000, 3000, [(1, 1000, 1500), (2, 1500, 2000)]),
            ("19:42", 0, 3000, [(1, 2000, 2300), (2, 2300, 2600), (3, 2600, 3000)]),
        ],
    )
    take_two = _word_reading(
        "r56",
        [
            (
                "19:42",
                3100,
                5000,
                [(2, 3100, 3500), (3, 3500, 4000), (4, 4000, 4500), (5, 4500, 5000)],
            )
        ],
    )
    projected = project_word_shard(_word_shard([take_one, take_two]))

    kept, dropped = select_complete_verses(projected, {(19, 40): 2, (19, 41): 2, (19, 42): 5})
    assert dropped == []
    assert [w["index"] for w in kept["19:42"]["words"]] == [1, 2, 3, 2, 3, 4, 5]
    assert kept["19:42"]["verse_start_ms"] == 2000
    # A verse that really was interrupted still splits: 19:41 starts between two takes of 19:40.
    starts = {ref: [row["start_ms"] for row in kept[ref]["segments"]] for ref in ("19:40", "19:41")}
    assert starts == {"19:40": [0], "19:41": [1000]}


def test_builder_delegates_to_staged_sdk(monkeypatch):
    calls = []

    class FakeShards(types.ModuleType):
        def build_native_shards(self, doc, **kwargs):
            calls.append((doc, kwargs))
            return {1: {}}

    module = FakeShards("qua_sdk.integrations.shards")
    monkeypatch.setitem(sys.modules, "qua_sdk", types.ModuleType("qua_sdk"))
    monkeypatch.setitem(
        sys.modules, "qua_sdk.integrations", types.ModuleType("qua_sdk.integrations")
    )
    monkeypatch.setitem(sys.modules, "qua_sdk.integrations.shards", module)
    assert build_timestamp_shards({"raw": True}, audio_category="by_surah") == {1: {}}
    assert calls == [({"raw": True}, {"audio_category": "by_surah", "src_meta": None})]


def test_native_brotli_is_deterministic():
    shard = _shard([_reading("r1", [("1:1", (0, 100), [1])])])
    first = brotli_shard(shard)
    assert first == brotli_shard(shard)
    assert orjson.loads(brotli.decompress(first)) == shard


def test_validated_writer_rejects_invalid_shard_without_replacing_target(tmp_path):
    target = tmp_path / "1.json.br"
    target.write_bytes(b"previous")
    invalid = _shard([_reading("r1", [("1:1", (0, 100), [1])])])
    invalid["_meta"]["schema_version"] = 11

    with pytest.raises(ValidationError):
        write_validated_shard(target, invalid)

    assert target.read_bytes() == b"previous"


def test_validated_writer_emits_the_canonical_brotli_bytes(tmp_path):
    shard = _shard([_reading("r1", [("1:1", (0, 100), [1])])])
    target = tmp_path / "1.json.br"

    payload = write_validated_shard(target, shard)

    assert payload == validated_brotli_shard(shard)
    assert target.read_bytes() == payload


def test_release_projection_rejects_a_v13_identity_closure_gap(tmp_path):
    shard = _shard([_reading("r1", [("1:1", (0, 100), [1])])])
    shard["readings"][0]["render"]["a"][0][0] = 9
    (tmp_path / "1.json.br").write_bytes(brotli_shard(shard))

    with pytest.raises(V13AuditError, match="animation words"):
        load_canonical_verses(tmp_path)


def test_decoder_passes_reading_variants_through():
    reading = _reading("r1", [("18:1", (100, 400), [11, 12])])
    variant = {
        "id": "iwaja_qayyima",
        "chosen": "idraj",
        "words": [0, 1],
        "targets": [0, 1],
        "anchor": "boundary",
        "boundary": 1,
        "by": "scored",
        "score": 4.0,
        "affected": {"sakt": {"c": [5, 6], "s": [], "b": [1]}},
    }
    reading["variants"] = [variant]
    plain = _reading("r2", [("18:2", (500, 600), [1])])

    decoded = decode_document(_shard([reading, plain]))

    assert decoded["readings"][0]["variants"] == [variant]
    assert "variants" not in decoded["readings"][1]
