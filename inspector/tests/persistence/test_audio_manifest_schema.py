"""Round-trip tests for the ``AudioManifestSidecar`` Pydantic schema.

Schema lives at ``qua_shared/schemas/bucket/catalog.py``. The sidecar is the
per-delivery ``catalog/audio_manifest/<slug>.json`` artefact — a ``_meta``
provenance block plus a ``chapters`` map of stringified surah keys to
per-chapter URL/probe records.

These assert the canonical on-disk shape round-trips byte/shape-equal via
``model_dump(by_alias=True)`` (the ``_meta`` alias must survive) and that the
artefact is pure ``extra="forbid"`` — any unexpected top-level key raises
``ValidationError`` rather than being stripped.
"""

from __future__ import annotations

import pytest

from qua_shared.schemas import AudioManifestSidecar


def _canonical_sidecar() -> dict:
    """Canonical ``by_surah`` sidecar — ``_meta`` alias + two chapters."""
    return {
        "schema_version": 1,
        "slug": "abdul_basit_murattal",
        "_meta": {
            "checksum": "sha256:9f1c0d2e",
            "source_meta_reciter": "abdulBasit",
            "source_manifest_path": "catalog/audio_manifest/abdul_basit_murattal.json",
            "chapter_count": 2,
            "category": "by_surah",
        },
        "chapters": {
            "1": {
                "url": "https://audio-cdn.tarteel.ai/quran/surah/abdulBasit/murattal/mp3/001.mp3",
                "size_bytes": 482301,
                "duration_sec": 41,
                "bitrate_kbps": 96,
                "bitrate_mode": "cbr",
                "max_linear_seek_err_ms": 12,
                "source_url": None,
                "source_offset_ms": None,
            },
            "112": {
                "url": "https://audio-cdn.tarteel.ai/quran/surah/abdulBasit/murattal/mp3/112.mp3",
                "size_bytes": 120044,
                "duration_sec": 10,
                "bitrate_kbps": 96,
                "bitrate_mode": "vbr",
                "max_linear_seek_err_ms": 8,
                "source_url": None,
                "source_offset_ms": None,
            },
        },
    }


# -- Validation tests ---------------------------------------------------


def test_canonical_sidecar_validates():
    m = AudioManifestSidecar.model_validate(_canonical_sidecar())
    assert m.slug == "abdul_basit_murattal"
    assert m.meta.checksum == "sha256:9f1c0d2e"
    assert m.meta.chapter_count == 2
    assert set(m.chapters) == {"1", "112"}
    assert m.chapters["1"].url.endswith("001.mp3")
    assert m.chapters["112"].bitrate_mode is not None
    assert m.chapters["112"].bitrate_mode.value == "vbr"


def test_schema_version_required():
    """``schema_version`` is the canonical on-disk discriminator. It has a
    default, but the canonical artefact always carries it — assert it
    survives the round-trip rather than silently defaulting."""
    raw = _canonical_sidecar()
    m = AudioManifestSidecar.model_validate(raw)
    assert m.schema_version == 1
    assert m.model_dump(by_alias=True)["schema_version"] == 1


def test_missing_meta_fails():
    """``_meta`` is required (no default) — a sidecar without it is corrupt."""
    raw = _canonical_sidecar()
    raw.pop("_meta")
    with pytest.raises(ValueError):
        AudioManifestSidecar.model_validate(raw)


def test_invalid_slug_fails():
    raw = _canonical_sidecar()
    raw["slug"] = "Not A Slug"
    with pytest.raises(ValueError, match="invalid slug"):
        AudioManifestSidecar.model_validate(raw)


# -- pure extra="forbid" policy ----------------------------------------


def test_unknown_top_level_key_rejected():
    """An unknown top-level key raises ``ValidationError`` under pure
    ``extra="forbid"`` — no silent strip, no absorbed extra."""
    raw = _canonical_sidecar()
    raw["unexpected_bloat"] = {"junk": True}
    with pytest.raises(ValueError):
        AudioManifestSidecar.model_validate(raw)


# -- Round-trip emission ------------------------------------------------


def test_round_trip_byte_shape_equal():
    """``model_dump(by_alias=True)`` reproduces the canonical artefact
    shape exactly — ``_meta`` alias preserved, no field renamed or dropped,
    no extra key introduced. Enum values serialize back to their string
    form so the dump is byte-equal to the input JSON shape."""
    raw = _canonical_sidecar()
    m = AudioManifestSidecar.model_validate(raw)
    out = m.model_dump(by_alias=True, mode="json")

    assert "_meta" in out and "meta" not in out
    assert out == raw


def test_combined_file_source_provenance_round_trips():
    """A combined-file intake (one source mp3 → several chapters) carries
    ``source_url`` + ``source_offset_ms`` per chapter; both survive the
    round-trip and the validators (``min_length``, ``ge=0``) accept them."""
    raw = _canonical_sidecar()
    raw["chapters"]["1"]["source_url"] = "https://drive.google.com/uc?id=abc123"
    raw["chapters"]["1"]["source_offset_ms"] = 0
    raw["chapters"]["112"]["source_url"] = "https://drive.google.com/uc?id=abc123"
    raw["chapters"]["112"]["source_offset_ms"] = 41_000
    m = AudioManifestSidecar.model_validate(raw)
    assert m.chapters["112"].source_offset_ms == 41_000
    assert m.model_dump(by_alias=True, mode="json") == raw


def test_chapters_default_empty():
    """A sidecar with no probed chapters yet still validates — ``chapters``
    defaults to an empty dict."""
    raw = _canonical_sidecar()
    raw.pop("chapters")
    m = AudioManifestSidecar.model_validate(raw)
    assert m.chapters == {}
    assert m.model_dump(by_alias=True)["chapters"] == {}


# -- URL hygiene (issue #285) -------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/srv/scratch/z5344896/qua_offline/intake/audio/x/001.mp3",
        r"C:\Users\me\audio\001.mp3",
        "file:///tmp/001.mp3",
        "~/audio/001.mp3",
    ],
)
def test_chapter_url_rejects_local_paths(url):
    raw = _canonical_sidecar()
    raw["chapters"]["1"]["url"] = url
    with pytest.raises(ValueError, match="local filesystem path"):
        AudioManifestSidecar.model_validate(raw)


def test_chapter_url_accepts_a_bucket_relative_key():
    # Fixtures mode seeds prefetched chapters by bucket key, not URL.
    raw = _canonical_sidecar()
    raw["chapters"]["1"]["url"] = "reciters/abdul_basit_murattal/audio/1.mp3"
    assert AudioManifestSidecar.model_validate(raw).chapters["1"].url.startswith("reciters/")


def test_chapter_source_url_must_be_remote():
    raw = _canonical_sidecar()
    raw["chapters"]["1"]["source_url"] = "/srv/scratch/x/001.mp3"
    with pytest.raises(ValueError, match="http"):
        AudioManifestSidecar.model_validate(raw)
