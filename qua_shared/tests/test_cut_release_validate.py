"""Regression: the cut's boundary-validation input must respect a real
``verse_start_ms == 0`` and keep its three fields consistent.

A canonical verse can legitimately start at 0 ms while its first word's audio
starts a few ms later (leading gap). The bounds the cut validates are the shared
layout's HF clip window (``build_verse_layouts``), and ``_verse_for_validate``
derives ``duration_ms`` from those same bounds — so the three fields always
agree (no phantom ``duration_arithmetic``, which once aborted the cut on
``abu_bakr_al_shatri_tarteel`` 5:1). GH occurrence bounds and HF clip bounds are
different public views derived from the same underlying layout.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from qua_jobs import cut_release
from qua_jobs.cut_release import _verse_for_validate
from qua_shared.dataset_validation import (
    check_duration_arithmetic,
)
from qua_shared.verse_layout import PadParams, build_verse_layouts, reshape_canonical

# Zero pads → the clip window equals the word-span, so these tier/validate tests
# read the same bounds the old word-span builder produced.
_ZERO_PADS: PadParams = {"pad_start": 0, "pad_end": 0, "min_gap": 0}


def _native_projection(verses: dict) -> dict:
    """Express compact test rows as the native canonical projection shape."""
    out = {}
    for ref, body in verses.items():
        words = []
        for row in body["words"]:
            specs = row[3] if len(row) > 3 else []
            source_text = "".join(text for text, _start, _end in specs) or "x"
            letters = []
            cursor = 0
            for sound_id, (text, start, end) in enumerate(specs):
                offsets = list(range(cursor, cursor + len(text)))
                letters.append(
                    {
                        "text": text,
                        "character_offsets": offsets,
                        "paint_character_offsets": offsets,
                        "sound_ids": [sound_id],
                        "start_ms": start,
                        "end_ms": end,
                    }
                )
                cursor += len(text)
            words.append(
                {
                    "index": row[0],
                    "ref": f"{ref}:{row[0]}",
                    "source_text": source_text,
                    "start_ms": row[1],
                    "end_ms": row[2],
                    "letters": letters,
                }
            )
        out[ref] = {
            "words": words,
            "verse_start_ms": body.get("verse_start_ms", min(row[1] for row in body["words"])),
            "verse_end_ms": body.get("verse_end_ms", max(row[2] for row in body["words"])),
            "segments": [],
        }
    return out


def _layouts(verses: dict) -> dict:
    projected = _native_projection(verses)
    digital_khatt = {
        word["ref"]: {"text": word["source_text"]}
        for verse in projected.values()
        for word in verse["words"]
    }
    return build_verse_layouts(reshape_canonical(projected, digital_khatt), **_ZERO_PADS)


def _canonical_occurrences(layouts: dict) -> list[dict]:
    return [{"ref": ref, "canonical": True, "layout": layout} for ref, layout in layouts.items()]


def _tiers(verses: dict, *, with_letters: bool = True) -> dict:
    return cut_release._build_tier_files(
        "example_reciter",
        _canonical_occurrences(_layouts(verses)),
        delivery_meta={"audio_category": "by_surah"},
        script_id="digital_khatt_v2",
        script_sha256="0" * 64,
        with_letters=with_letters,
    )


def test_verse_start_zero_with_leading_word_gap():
    # verse_start_ms is a real 0; first word's audio starts at 60 ms. With zero
    # pads the clip window is [0, 24095] — the real 0 is respected (not coerced
    # to the word start), and duration == end - start.
    verses = {
        "5:1": {
            "verse_start_ms": 0,
            "verse_end_ms": 24095,
            "words": [[1, 60, 2350], [23, 21995, 24095]],
        }
    }
    out = _verse_for_validate(_layouts(verses)["5:1"])
    assert out["verse_start_ms"] == 0
    assert out["verse_end_ms"] == 24095
    assert out["duration_ms"] == 24095
    assert check_duration_arithmetic("5:1", out) == []  # no phantom violation


def test_nonzero_start_duration_consistent():
    verses = {"22:1": {"verse_start_ms": 120, "verse_end_ms": 12814, "words": [[1, 120, 12814]]}}
    out = _verse_for_validate(_layouts(verses)["22:1"])
    assert out["duration_ms"] == 12694
    assert check_duration_arithmetic("22:1", out) == []


def test_release_timestamp_tiers_preserve_verse_order():
    verses = {
        "1:1": {"words": [[1, 0, 100]]},
        "2:1": {"words": [[1, 100, 200]]},
        "10:1": {"words": [[1, 200, 300]]},
        "100:1": {"words": [[1, 300, 400]]},
    }
    files = _tiers(verses)
    for name in (
        "verse_timestamps.json.gz",
        "word_timestamps.json.gz",
        "letter_timestamps.json.gz",
    ):
        doc = json.loads(gzip.decompress(files[name]).decode("utf-8"))
        assert [row[0] for row in doc["rows"]] == ["1:1", "2:1", "10:1", "100:1"]


def test_a_word_profile_delivery_ships_no_letter_tier():
    """A proxy-timed recitation has no letter geometry at all, so the tier is
    absent rather than emitted empty — a consumer must be able to tell "this
    recitation has no letter timings" from "this verse happened to have none"."""
    verses = {"1:1": {"words": [[1, 0, 100]]}, "1:2": {"words": [[1, 100, 200]]}}
    files = _tiers(verses, with_letters=False)

    assert set(files) == {"verse_timestamps.json.gz", "word_timestamps.json.gz"}
    word_doc = json.loads(gzip.decompress(files["word_timestamps.json.gz"]).decode("utf-8"))
    assert [row[0] for row in word_doc["rows"]] == ["1:1", "1:2"]


def test_a_word_profile_tier_names_the_editions_script():
    """The `_meta.script` of a non-Hafs delivery must not claim Digital Khatt —
    a reader would then typeset the words in the wrong font."""
    verses = {"1:1": {"words": [[1, 0, 100]]}}
    files = cut_release._build_tier_files(
        "example_reciter",
        _canonical_occurrences(_layouts(verses)),
        delivery_meta={"audio_category": "by_surah"},
        script_id="qaloon-v21+sdk-words-v1",
        script_sha256="a" * 64,
        riwayah="qalun",
        with_letters=False,
    )
    doc = json.loads(gzip.decompress(files["word_timestamps.json.gz"]).decode("utf-8"))
    assert doc["_meta"]["script"] == "qaloon-v21+sdk-words-v1"
    assert doc["_meta"]["script_sha256"] == "a" * 64
    assert doc["_meta"]["riwayah"] == "qalun"


def test_letter_tier_keeps_digital_khatt_text_and_scalar_paint_ranges():
    verses = {
        "19:1": {
            "words": [
                [
                    1,
                    0,
                    500,
                    [
                        ["كٓ", 0, 100],
                        ["ه", 100, 200],
                        ["ي", 200, 300],
                        ["عٓ", 300, 400],
                        ["صٓ", 400, 500],
                    ],
                ]
            ]
        }
    }
    files = _tiers(verses)
    doc = json.loads(gzip.decompress(files["letter_timestamps.json.gz"]).decode("utf-8"))
    row = doc["rows"][0]
    assert row[6] == "كٓهيعٓصٓ"
    tokens = row[7]
    assert tokens[0] == [0, 0, 100, True, [[0, 2]]]
    assert tokens[-1] == [0, 400, 500, True, [[6, 8]]]


def test_letter_tier_preserves_combining_marks_without_a_vocab():
    verses = {"1:1": {"words": [[1, 0, 100, [["بَ", 0, 100]]]]}}
    doc = json.loads(gzip.decompress(_tiers(verses)["letter_timestamps.json.gz"]))
    assert doc["rows"][0][6] == "بَ"
    assert doc["rows"][0][7] == [[0, 0, 100, True, [[0, 2]]]]


def test_release_verse_bound_is_audible_span_not_hf_clip_window():
    # The GH occurrence timeline uses last-audible bounds and exposes following
    # silence separately. HF remains free to cut its padded clip from the shared
    # layout without changing this public release contract.
    verses = {
        "1:1": {"words": [[1, 100, 1000]], "verse_start_ms": 100, "verse_end_ms": 1000},
        "1:2": {"words": [[1, 5000, 6000]], "verse_start_ms": 5000, "verse_end_ms": 6000},
    }
    projected = _native_projection(verses)
    digital_khatt = {
        word["ref"]: {"text": word["source_text"]}
        for verse in projected.values()
        for word in verse["words"]
    }
    layouts = build_verse_layouts(
        reshape_canonical(projected, digital_khatt),
        pad_start=100,
        pad_end=300,
        min_gap=100,
    )
    files = cut_release._build_tier_files(
        "example_reciter",
        _canonical_occurrences(layouts),
        delivery_meta={"audio_category": "by_surah"},
        script_id="digital_khatt_v2",
        script_sha256="0" * 64,
    )
    verse_doc = json.loads(gzip.decompress(files["verse_timestamps.json.gz"]).decode("utf-8"))
    assert verse_doc["rows"][0] == ["1:1", 100, 1000, True, 4000]
    # The word tier keeps the true source-relative word times.
    word_doc = json.loads(gzip.decompress(files["word_timestamps.json.gz"]).decode("utf-8"))
    assert word_doc["rows"][0][5] == [[1, 100, 1000]]


def test_release_tiers_share_occurrence_prefix_and_compute_silence_after():
    verses = {
        "1:1": {"words": [[1, 100, 1000]], "verse_start_ms": 100, "verse_end_ms": 1000},
        "1:2": {"words": [[1, 1500, 2000]], "verse_start_ms": 1500, "verse_end_ms": 2000},
        "2:1": {"words": [[1, 0, 500]], "verse_start_ms": 0, "verse_end_ms": 500},
    }
    files = _tiers(verses)
    verse = json.loads(gzip.decompress(files["verse_timestamps.json.gz"]))
    word = json.loads(gzip.decompress(files["word_timestamps.json.gz"]))
    letter = json.loads(gzip.decompress(files["letter_timestamps.json.gz"]))

    assert verse["rows"][0] == word["rows"][0][:5] == letter["rows"][0][:5]
    assert word["rows"][0] == letter["rows"][0][:6]
    assert verse["rows"] == [
        ["1:1", 100, 1000, True, 500],
        ["1:2", 1500, 2000, True, 0],
        ["2:1", 0, 500, True, 0],
    ]
    assert verse["_meta"]["units"] == "ms"
    assert verse["_meta"]["riwayah"] == "hafs"
    assert verse["_meta"]["schema_version"] == 3


def test_release_tiers_emit_every_occurrence_in_timeline_order():
    """A repeat of 1:1 recited after 1:2 is its own unflagged row at its place
    in the timeline; silence_after follows the timeline, not the mushaf; the
    verse count is distinct refs while the occurrence count is rows."""
    verses = {
        "1:1": {"words": [[1, 100, 1000]], "verse_start_ms": 100, "verse_end_ms": 1000},
        "1:2": {"words": [[1, 1500, 2000]], "verse_start_ms": 1500, "verse_end_ms": 2000},
    }
    repeat = {"1:1": {"words": [[1, 2500, 3000]], "verse_start_ms": 2500, "verse_end_ms": 3000}}
    occurrences = _canonical_occurrences(_layouts(verses)) + [
        {"ref": "1:1", "canonical": False, "layout": _layouts(repeat)["1:1"]}
    ]
    files = cut_release._build_tier_files(
        "example_reciter",
        occurrences,
        delivery_meta={"audio_category": "by_surah"},
        script_id="digital_khatt_v2",
        script_sha256="0" * 64,
    )
    verse = json.loads(gzip.decompress(files["verse_timestamps.json.gz"]))
    word = json.loads(gzip.decompress(files["word_timestamps.json.gz"]))
    assert verse["rows"] == [
        ["1:1", 100, 1000, True, 500],
        ["1:2", 1500, 2000, True, 500],
        ["1:1", 2500, 3000, False, 0],
    ]
    assert word["rows"][2] == ["1:1", 2500, 3000, False, 0, [[1, 2500, 3000]]]
    assert verse["_meta"]["verse_count"] == 2
    assert verse["_meta"]["occurrence_count"] == 3


def test_release_tiers_refuse_a_verse_without_exactly_one_canonical_row():
    verses = {"1:1": {"words": [[1, 100, 1000]]}}
    doubled = _canonical_occurrences(_layouts(verses)) * 2
    with pytest.raises(ValueError, match="exactly one canonical"):
        cut_release._build_tier_files(
            "example_reciter",
            doubled,
            delivery_meta={"audio_category": "by_surah"},
            script_id="digital_khatt_v2",
            script_sha256="0" * 64,
        )


# ---------------------------------------------------------------------------
# DigitalKhatt asset validation and manifest hashing.
# ---------------------------------------------------------------------------


def test_digital_khatt_assets_validate_and_hash(tmp_path):
    (tmp_path / "surah_info.json").write_bytes(b'{"surahs": []}')
    script = b'{"1:1:1":{"id":1,"surah":"1","ayah":"1","word":"1","location":"1:1:1","text":"x"}}'
    script_dir = tmp_path / "data"
    font_dir = tmp_path / "inspector" / "frontend" / "public" / "fonts"
    script_dir.mkdir()
    font_dir.mkdir(parents=True)
    (script_dir / "digital_khatt_v2_script.json").write_bytes(script)
    (font_dir / "DigitalKhattV2.otf").write_bytes(b"font")
    loaded_script, loaded_font = cut_release._load_digital_khatt_assets(tmp_path)
    out = cut_release._hash_static_refs(
        tmp_path,
        {
            "digital_khatt_v2_script.json": loaded_script,
            "DigitalKhattV2.otf": loaded_font,
        },
    )
    assert out["digital_khatt_v2_script.json"]["sha256"] == cut_release._sha256_hex(script)
    assert out["DigitalKhattV2.otf"]["bytes"] == 4
    assert "surah_info.json" in out


def test_schema_three_cut_starts_release_format_v4():
    unchanged = [{"change_kind": "unchanged"}]
    assert cut_release._compute_version("v3.4.0", unchanged, False, None) == "v4.0.0"


def test_release_format_v4_keeps_normal_bumps_and_rejects_old_override():
    assert (
        cut_release._compute_version("v4.0.0", [{"change_kind": "added"}], False, None) == "v4.1.0"
    )
    assert (
        cut_release._compute_version("v4.1.0", [{"change_kind": "refresh"}], False, None)
        == "v4.1.1"
    )
    with pytest.raises(RuntimeError, match="requires release v4"):
        cut_release._compute_version("v3.4.0", [], False, "v3.5.0")


def test_unchanged_v4_release_still_refuses_a_noop_cut():
    with pytest.raises(RuntimeError, match="nothing changed"):
        cut_release._compute_version("v4.0.0", [{"change_kind": "unchanged"}], False, None)


def test_audio_urls_come_from_sidecar_chapters():
    sidecar = {
        "schema_version": 1,
        "slug": "example_reciter",
        "_meta": {"checksum": "abc", "chapter_count": 1, "category": "by_surah"},
        "chapters": {
            "100": {
                "url": "https://cdn.example/100.mp3",
                "duration_sec": 60,
                "bitrate_mode": "cbr",
                "max_linear_seek_err_ms": 26,
            }
        },
    }

    urls, offsets = cut_release._audio_sources_from_manifest("example_reciter", sidecar)
    assert urls == {"100": "https://cdn.example/100.mp3"}
    assert offsets == {}  # CDN by-surah: no offsets


def test_audio_urls_tolerate_legacy_sidecar_metadata():
    sidecar = {
        "_meta": {"source": "intake_ingest_manual", "chapter_count": 1},
        "chapters": {
            "2": {
                "url": "https://cdn.example/2.mp3",
                "duration_sec": 8848.826,
                "max_linear_seek_err_ms": 26,
            }
        },
    }

    urls, offsets = cut_release._audio_sources_from_manifest("legacy_reciter", sidecar)
    assert urls == {"2": "https://cdn.example/2.mp3"}
    assert offsets == {}


def test_combined_source_surfaces_native_url_and_offset():
    # One Drive file serves chapters 1 + 2 (bucket path swapped into ``url``);
    # the release must point at the native ``source_url`` and carry the offset of
    # the chapter that starts partway into the file.
    sidecar = {
        "schema_version": 1,
        "slug": "combined_reciter",
        "_meta": {"checksum": "abc", "chapter_count": 2, "category": "by_surah"},
        "chapters": {
            "1": {
                "url": "https://bucket/reciters/combined_reciter/audio/1.mp3",
                "source_url": "https://drive.google.com/file/d/XYZ/view",
            },
            "2": {
                "url": "https://bucket/reciters/combined_reciter/audio/2.mp3",
                "source_url": "https://drive.google.com/file/d/XYZ/view",
                "source_offset_ms": 215000,
            },
        },
    }

    urls, offsets = cut_release._audio_sources_from_manifest("combined_reciter", sidecar)
    assert urls == {
        "1": "https://drive.google.com/file/d/XYZ/view",
        "2": "https://drive.google.com/file/d/XYZ/view",
    }
    assert offsets == {"2": 215000}  # only the non-zero offset is emitted


def test_single_file_offset_emitted_without_source_url():
    # A unique-per-chapter source whose recitation starts after a lead-in: the
    # URL is already native (no ``source_url``) but the offset must still ship.
    sidecar = {
        "_meta": {"checksum": "abc", "chapter_count": 1, "category": "by_surah"},
        "chapters": {
            "36": {
                "url": "https://drive.google.com/file/d/ABC/view",
                "source_offset_ms": 4200,
            }
        },
    }

    urls, offsets = cut_release._audio_sources_from_manifest("single_file_reciter", sidecar)
    assert urls == {"36": "https://drive.google.com/file/d/ABC/view"}
    assert offsets == {"36": 4200}


def test_empty_audio_urls_are_fatal_for_catalog_build():
    rec = {"slug": "example_reciter", "audio_category": "by_surah"}
    verses = {"100:1": {"words": [[1, 0, 100]]}}
    sidecar = {
        "schema_version": 1,
        "slug": "example_reciter",
        "_meta": {"checksum": "abc", "chapter_count": 0, "category": "by_surah"},
        "chapters": {},
    }

    with pytest.raises(RuntimeError, match="no usable audio URLs"):
        cut_release._build_catalog_json(rec, sidecar, verses)


# ---------------------------------------------------------------------------
# Shards vs the catalog row.
# ---------------------------------------------------------------------------


def test_a_row_that_agrees_across_the_two_vocabularies_cuts():
    # The delivery FK holds the long Inspector slug; the shard `_meta` holds the
    # SDK one. Same edition, so this must not be read as a mismatch.
    cut_release._assert_riwayat_agree("slug", "warsh", "warsh_an_nafi")
    cut_release._assert_riwayat_agree("slug", "warsh", "warsh")


def test_an_absent_row_riwayah_is_not_evidence():
    # Rows written before the column existed are Hafs by construction.
    cut_release._assert_riwayat_agree("slug", "hafs", None)


def test_shards_disagreeing_with_the_row_abort_the_cut():
    # The row is what the HF dataset config, the request form and the manifest's
    # `riwayah_name` key on — cutting anyway files one edition's timings under
    # another edition's name.
    with pytest.raises(ValueError, match="mislabelled"):
        cut_release._assert_riwayat_agree("slug", "qalun", "warsh_an_nafi")


def test_an_unsupported_row_riwayah_aborts_the_cut():
    with pytest.raises(ValueError, match="unsupported riwayah"):
        cut_release._assert_riwayat_agree("slug", "hafs", "duri_an_abi_amr")


def test_edition_assets_ship_script_and_font_per_non_hafs_riwayah():
    """A Warsh release uploads Warsh's own word script + font beside the Digital
    Khatt pair; Hafs contributes nothing; the manifest names both files."""
    qua_domain = pytest.importorskip("qua_domain")

    assert cut_release._edition_assets({"hafs"}) == {}

    assets = cut_release._edition_assets({"hafs", "warsh"})
    assert set(assets) == {"warsh_words.json.gz", "warsh.ttf"}
    words = json.loads(gzip.decompress(assets["warsh_words.json.gz"]))
    assert words[0] == {"ref": "1:1:1", "text": words[0]["text"]}
    assert len(words) == qua_domain.get_edition("warsh").word_count
    assert assets["warsh.ttf"] == qua_domain.read_font_asset("warsh")

    editions = cut_release._release_editions({"warsh"})
    assert editions["warsh"]["words_asset"] == "warsh_words.json.gz"
    assert editions["warsh"]["font_asset"] == "warsh.ttf"

    refs = cut_release._hash_static_refs(Path("/nonexistent"), assets)
    assert refs["warsh.ttf"]["sha256"] == qua_domain.get_edition("warsh").font.sha256
    assert cut_release._static_content_type("warsh.ttf") == "font/ttf"
    assert cut_release._static_content_type("warsh_words.json.gz") == "application/gzip"
    assert cut_release._static_content_type("DigitalKhattV2.otf") == "font/otf"
    assert cut_release._static_content_type("surah_info.json") == "application/json"
