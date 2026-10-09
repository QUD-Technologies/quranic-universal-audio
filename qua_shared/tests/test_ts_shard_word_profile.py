"""The word-profile timestamp shard (schema 14) and its audit."""

from __future__ import annotations

import copy
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from qua_shared.schemas.bucket.ts_shard import (
    TS_SHARD_SCHEMA_VERSION,
    TsShardDoc,
    TsWordShardDoc,
)
from qua_shared.timestamps_shards import (
    NATIVE_SHARD_SCHEMA_VERSIONS,
    brotli_shard,
    parse_shard,
    shard_profile,
    validated_brotli_shard,
)
from qua_shared.timestamps_word_audit import WordAuditError, audit_word_document


def _installed_edition() -> dict:
    """Real ids + digests when ``qua_domain`` is installed, else placeholders.

    The audit refuses a shard whose ``words_sha256`` disagrees with the
    installed index, so a fixture with an invented digest would exercise only
    that rejection. Reading the real values keeps every audit test below on the
    path a producer actually takes, while a runtime without the package (a fork)
    still collects — the audit skips the coordinate check there.
    """
    try:
        import qua_domain
    except ImportError:
        return {
            "edition_id": "qaloon-v21+sdk-words-v1",
            "words_sha256": "a" * 64,
            "reference_id": "qul-text-qpc-hafs-312",
            "projection_id": "qua-edition-projection-v1",
            "projection_sha256": "b" * 64,
        }
    edition = qua_domain.get_edition("qalun")
    return {
        "edition_id": edition.edition_id,
        "words_sha256": edition.words_sha256,
        "reference_id": qua_domain.REFERENCE_ID,
        "projection_id": qua_domain.PROJECTION_ID,
        "projection_sha256": qua_domain.projection_asset_info().sha256,
    }


_REFS = ("112:1:1", "112:1:2", "112:1:3", "112:1:4")


def _installed_text() -> tuple[str, ...]:
    """Qalun's own spelling of 112:1, or placeholders without the package.

    The audit compares every row's text against the edition index, so inventing
    it here would exercise only the rejection. Qalun writes ``الله`` differently
    from Hafs — that difference is the whole reason the field exists.
    """
    try:
        from qua_domain import load_edition_index
    except ImportError:
        return tuple(f"w{n}" for n in range(1, len(_REFS) + 1))
    index = {word.ref: word.text for word in load_edition_index("qalun").words}
    return tuple(index[ref] for ref in _REFS)


_TEXT = _installed_text()

WORD_SHARD: dict = {
    "_meta": {
        "schema_version": 14,
        "profile": "word",
        "chapter": 112,
        "audio_category": "by_surah",
        "riwayah": "qalun",
        "timing_provider": "hafs_proxy_mfa",
        "reference_riwayah": "hafs",
        **_installed_edition(),
    },
    "readings": [
        {
            "id": "r1",
            "parts": [["112:1", 0, 3000, 0, 4]],
            "words": [
                [_REFS[0], _TEXT[0], 0, 700],
                [_REFS[1], _TEXT[1], 700, 1500],
                [_REFS[2], _TEXT[2], 1500, 2200],
                [_REFS[3], _TEXT[3], 2200, 3000],
            ],
            "boundaries": [[1, None], [1, None], [1, None], [3, 1]],
        }
    ],
}


def _shard(**meta_overrides) -> dict:
    doc = copy.deepcopy(WORD_SHARD)
    doc["_meta"].update(meta_overrides)
    return doc


# -- Discrimination ---------------------------------------------------------


def test_absent_profile_reads_as_native():
    """Every existing v13 object predates ``profile`` and must stay native."""
    assert shard_profile({"_meta": {"schema_version": 13}}) == "native"
    assert shard_profile({}) == "native"


def test_word_profile_is_discriminated():
    assert shard_profile(WORD_SHARD) == "word"
    assert isinstance(parse_shard(WORD_SHARD), TsWordShardDoc)


def test_native_meta_accepts_both_schema_versions():
    """v13 objects are never restamped, so the reader accepts 13 and 14."""
    assert NATIVE_SHARD_SCHEMA_VERSIONS == (13, 14)
    assert TS_SHARD_SCHEMA_VERSION == 14
    meta_model = TsShardDoc.model_fields["meta"].annotation
    assert isinstance(meta_model, type) and issubclass(meta_model, BaseModel)
    fields = meta_model.model_fields
    assert get_args(fields["schema_version"].annotation) == (13, 14)


def test_native_meta_declares_no_profile_field():
    """The shard route serves native bytes verbatim, so a defaulted ``profile``
    would rewrite 4,126 existing objects on the first round-trip. Native is the
    absence of the key; ``shard_profile`` reads it from the raw dict."""
    meta_model = TsShardDoc.model_fields["meta"].annotation
    assert isinstance(meta_model, type) and issubclass(meta_model, BaseModel)
    assert "profile" not in meta_model.model_fields
    native = {
        "_meta": {
            "schema_version": 13,
            "chapter": 1,
            "audio_category": "by_surah",
            "phonemizer_version": "3.0",
            "native_schema_version": 2,
            "renderer_codec_version": 1,
            "native_profile": {
                "riwayah": "hafs",
                "script": "uthmani",
                "variant": {},
                "extra_phonemes": [],
            },
        },
        "readings": [],
    }
    doc = TsShardDoc.model_validate(native)
    assert doc.model_dump(mode="json", by_alias=True) == native


# -- Round-trip -------------------------------------------------------------


def test_round_trip_is_byte_equal():
    doc = TsWordShardDoc.model_validate(WORD_SHARD)
    assert doc.model_dump(mode="json", by_alias=True, exclude_none=True) == WORD_SHARD


def test_meta_carries_unknown_provenance_through_unchanged():
    """``extra="allow"`` on the meta is the documented forward-compat exception —
    the producer may add provenance the current readers ignore."""
    raw = _shard(mfa_model_sha256="c" * 64)
    doc = TsWordShardDoc.model_validate(raw)
    assert doc.model_dump(mode="json", by_alias=True, exclude_none=True) == raw


def test_readings_stay_closed():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["render"] = {}
    with pytest.raises(ValidationError):
        TsWordShardDoc.model_validate(raw)


# -- Closure failures -------------------------------------------------------


def test_boundary_count_must_match_word_count():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["boundaries"].pop()
    with pytest.raises(ValidationError, match="boundary counts differ"):
        TsWordShardDoc.model_validate(raw)


def test_part_cannot_reference_unknown_words():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["parts"] = [["112:1", 0, 3000, 0, 9]]
    with pytest.raises(ValidationError, match="unknown words"):
        TsWordShardDoc.model_validate(raw)


def test_word_interval_must_be_ordered():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][1] = ["112:1:2", "w2", 1500, 700]
    with pytest.raises(ValidationError, match="end precedes start"):
        TsWordShardDoc.model_validate(raw)


def test_unknown_boundary_state_rejected():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["boundaries"][0] = [9, None]
    with pytest.raises(ValidationError, match="boundary state"):
        TsWordShardDoc.model_validate(raw)


# -- Audit ------------------------------------------------------------------


def test_audit_accepts_a_well_formed_shard():
    assert audit_word_document(WORD_SHARD).meta.riwayah == "qalun"


@pytest.mark.skipif(
    _installed_edition()["words_sha256"] == "a" * 64,
    reason="qua-domain not installed (the audit skips the coordinate check)",
)
def test_audit_rejects_a_shard_built_against_a_different_script_revision():
    # The whole point of carrying words_sha256: a shard whose text came from
    # one revision of the edition and whose refs came from another would time
    # the wrong words, silently.
    with pytest.raises(WordAuditError, match="different revisions"):
        audit_word_document(_shard(words_sha256="a" * 64))


@pytest.mark.skipif(
    _installed_edition()["words_sha256"] == "a" * 64,
    reason="qua-domain not installed (the audit skips the coordinate check)",
)
def test_audit_rejects_a_ref_that_is_not_a_word_of_this_edition():
    raw = copy.deepcopy(WORD_SHARD)
    # 112:1 has four words in every edition; a fifth does not exist.
    raw["readings"][0]["words"][3] = ["112:1:5", "w5", 2200, 3000]
    with pytest.raises(WordAuditError, match="is not a qalun word"):
        audit_word_document(raw)


def test_audit_rejects_words_outside_their_part():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][3] = ["112:1:4", "w4", 2200, 9000]
    with pytest.raises(WordAuditError, match="escapes its part"):
        audit_word_document(raw)


def test_audit_rejects_overlapping_words():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][2] = ["112:1:3", "w3", 600, 2200]
    with pytest.raises(WordAuditError, match="before the previous word ended"):
        audit_word_document(raw)


def test_audit_rejects_uncovered_words():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["parts"] = [["112:1", 0, 1500, 0, 2]]
    with pytest.raises(WordAuditError, match="not covered by any part"):
        audit_word_document(raw)


def test_audit_rejects_empty_word_text():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][0] = ["112:1:1", "", 0, 700]
    with pytest.raises(WordAuditError, match="empty text"):
        audit_word_document(raw)


def test_audit_rejects_duplicate_reading_ids():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"].append(copy.deepcopy(raw["readings"][0]))
    with pytest.raises(WordAuditError, match="duplicate reading id"):
        audit_word_document(raw)


def test_special_ordinals_bypass_the_coordinate_check():
    """An unnumbered opener is timed as ``0:0:N`` — not a target coordinate."""
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][0] = ["0:0:1", "basmala", 0, 700]
    assert audit_word_document(raw)


# -- Writer dispatch --------------------------------------------------------


def test_validated_brotli_shard_routes_to_the_word_audit():
    payload = validated_brotli_shard(WORD_SHARD)
    assert payload == brotli_shard(WORD_SHARD)


def test_validated_brotli_shard_refuses_a_bad_word_shard():
    raw = copy.deepcopy(WORD_SHARD)
    raw["readings"][0]["words"][0] = ["112:1:1", "w1", 0, 9999]
    with pytest.raises(WordAuditError):
        validated_brotli_shard(raw)


# -- The text check ---------------------------------------------------------


@pytest.mark.skipif(
    _TEXT[0].startswith("w"), reason="qua_domain absent — the audit skips the text check"
)
def test_audit_rejects_a_row_whose_text_is_not_the_editions():
    """``words_sha256`` pins the index revision, not that a row came from it."""
    doc = copy.deepcopy(WORD_SHARD)
    doc["readings"][0]["words"][2][1] = "ٱللَّهُ"  # Hafs's spelling, under Qalun coordinates

    with pytest.raises(WordAuditError, match="does not write"):
        audit_word_document(doc)


@pytest.mark.skipif(
    _TEXT[0].startswith("w"), reason="qua_domain absent — the audit skips the text check"
)
def test_audit_rejects_a_word_profile_that_claims_to_be_hafs():
    """Hafs is timed natively; a word profile would discard its cells."""
    doc = _shard(riwayah="hafs")

    # The schema refuses it first, but the audit is the single door every
    # caller catches on — so it must still surface as a WordAuditError.
    with pytest.raises(WordAuditError, match="riwayah"):
        audit_word_document(doc)
