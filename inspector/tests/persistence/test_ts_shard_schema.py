"""Strict round-trip tests for compact native timestamp shards (v13, v15)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from qua_shared.schemas import TsShardDoc


def _doc() -> dict:
    return {
        "_meta": {
            "schema_version": 13,
            "chapter": 1,
            "audio_category": "by_surah",
            "phonemizer_version": "2.15.0",
            "native_schema_version": 2,
            "renderer_codec_version": 1,
            "native_profile": {
                "riwayah": "hafs",
                "script": "uthmani",
                "variant": {},
                "extra_phonemes": ["emphatic_fatha"],
            },
            "aligner_model": "mfa-arabic-v3",
        },
        "readings": [
            {
                "id": "r1",
                "parts": [["1:3", 100, 500, 0, 1]],
                "render": {
                    "v": 1,
                    "m": ["1:3:1", "canon", "native"],
                    "p": ["b"],
                    "r": [],
                    "w": [["1:3:1", "ب", [], [[0, [], []]], [], [], []]],
                    "b": [[3, [], [], [], 3, None]],
                    "a": [[0, [0], [0], [0], "ب", [0], 0, None]],
                },
                "timing": {
                    "w": [[100, 500]],
                    "s": [[100, 500]],
                    "a": [[100, 500]],
                    "c": [],
                },
            }
        ],
    }


def test_native_v13_round_trips_compact_storage():
    doc = _doc()
    model = TsShardDoc.model_validate(doc)
    assert model.model_dump(by_alias=True, mode="json") == doc


def test_meta_preserves_generation_provenance():
    model = TsShardDoc.model_validate(_doc())
    assert (model.meta.model_extra or {})["aligner_model"] == "mfa-arabic-v3"


def test_renderer_codec_version_is_guarded():
    doc = _doc()
    doc["readings"][0]["render"]["v"] = 2
    with pytest.raises(ValidationError):
        TsShardDoc.model_validate(doc)


def test_unknown_top_level_and_reading_fields_are_rejected():
    doc = _doc()
    doc["legacy_segments"] = []
    with pytest.raises(ValidationError):
        TsShardDoc.model_validate(doc)
    doc = _doc()
    doc["readings"][0]["share_group"] = 1
    with pytest.raises(ValidationError):
        TsShardDoc.model_validate(doc)


def test_invalid_timing_and_part_ranges_are_rejected():
    doc = _doc()
    doc["readings"][0]["timing"]["s"][0] = [100, 99]
    with pytest.raises(ValidationError, match="timing end precedes start"):
        TsShardDoc.model_validate(doc)
    doc = _doc()
    doc["readings"][0]["parts"][0] = ["1:3", 500, 100, 0, 1]
    with pytest.raises(ValidationError, match="invalid compact part"):
        TsShardDoc.model_validate(doc)


# -- v15 reading variants ----------------------------------------------------

_VARIANT = {
    "id": "iwaja_qayyima",
    "chosen": "idraj",
    "words": [0, 1],
    "targets": [0, 1],
    "anchor": "boundary",
    "boundary": 1,
    "by": "scored",
    "score": 4.0,
    "affected": {"sakt": {"c": [5, 6], "s": [1], "b": [1]}},
}

_CATALOGUE = {
    "iwaja_qayyima": {
        "name": "Iwaja qayyima",
        "description": None,
        "options": ["sakt", "idraj"],
        "default": "sakt",
    }
}


def _v15_doc() -> dict:
    """Two words, two sounds, boundaries 1–2, and one boundary-anchored variant."""
    doc = _doc()
    doc["_meta"]["schema_version"] = 15
    doc["_meta"]["variant_catalogue"] = copy.deepcopy(_CATALOGUE)
    doc["_meta"]["variant_policy"] = "2026-10-05"
    reading = doc["readings"][0]
    render = reading["render"]
    render["p"].append("a")
    render["w"].append(["1:3:2", "ا", [], [[1, [], []]], [], [], []])
    render["b"] = [[1, [], [], [], None, None], [3, [], [], [], 3, None]]
    render["a"].append([1, [1], [1], [1], "ا", [1], 0, None])
    reading["parts"][0][4] = 2
    reading["timing"]["w"] = [[100, 300], [300, 500]]
    reading["timing"]["s"] = [[100, 300], [300, 500]]
    reading["timing"]["a"] = [[100, 300], [300, 500]]
    reading["variants"] = [copy.deepcopy(_VARIANT)]
    return doc


def test_native_v15_with_variants_round_trips():
    doc = _v15_doc()
    model = TsShardDoc.model_validate(doc)
    assert model.model_dump(by_alias=True, mode="json") == doc
    variants = model.readings[0].variants
    assert variants is not None and variants[0].affected["sakt"].c == [5, 6]


def test_v13_dump_omits_absent_v15_fields():
    dumped = TsShardDoc.model_validate(_doc()).model_dump(by_alias=True, mode="json")
    assert "variants" not in dumped["readings"][0]
    assert "variant_catalogue" not in dumped["_meta"]


def test_variants_need_schema_15():
    doc = _v15_doc()
    doc["_meta"]["schema_version"] = 13
    with pytest.raises(ValidationError, match="need schema v15"):
        TsShardDoc.model_validate(doc)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda v, m: v.update(id="unlisted"), "not in the chapter catalogue"),
        (lambda v, m: v.update(chosen="other"), "names unknown options"),
        (lambda v, m: v["affected"].pop("sakt"), "names unknown options"),
        (lambda v, m: v["affected"].update(idraj=v["affected"]["sakt"]), "lists its chosen"),
        (lambda v, m: v.update(boundary=None), "boundary must be set exactly"),
        (lambda v, m: v.update(anchor="word"), "boundary must be set exactly"),
        (lambda v, m: v.update(words=[0, 2]), "references unknown words"),
        (lambda v, m: v.update(boundary=3), "anchors an unknown boundary"),
        (lambda v, m: v["affected"]["sakt"].update(b=[0]), "unknown sounds or boundaries"),
        (lambda v, m: v["affected"]["sakt"].update(s=[2]), "unknown sounds or boundaries"),
        (lambda v, m: v.update(by="manual"), "by"),
        (lambda v, m: m["iwaja_qayyima"].update(default="none"), "default is not an option"),
    ],
)
def test_invalid_variants_are_rejected(mutate, message):
    doc = _v15_doc()
    mutate(doc["readings"][0]["variants"][0], doc["_meta"]["variant_catalogue"])
    with pytest.raises(ValidationError, match=message):
        TsShardDoc.model_validate(doc)
