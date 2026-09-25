"""Reciter catalog schema: ``<bucket>/catalog/reciter_catalog.json``.

Single file consolidates vocab (riwayat, styles, sources, channels,
recording_contexts), reciters[], deliveries[], aliases[], plus a ``derived``
section of computed indices. Inspector backend is the sole writer.

Authoritative spec: docs/reference/catalog.md.

Phase-1 scope: enough to deserialize the vocab-only stub catalog produced
by ``.local/inspector-deploy/v2/seed_catalog_stub.py``. Full Delivery /
sidecar shapes are modeled here but won't be exercised until catalog
promotion lands (blocked on bulk audio probe).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from ..config.state import SLUG_RE

# Source slugs allow hyphens (e.g. ``surah-quran``); everything else uses
# the standard slug regex.
SOURCE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,79}$")


class AudioCategory(str, Enum):
    BY_SURAH = "by_surah"
    BY_AYAH = "by_ayah"


class BitrateMode(str, Enum):
    """Collapsed v2 enum. Legacy ``mostly_cbr`` / ``mostly_vbr`` / ``abr``
    were collapsed into ``mixed`` by migration 0014."""

    CBR = "cbr"
    VBR = "vbr"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ChapterBitrateMode(str, Enum):
    """Per-chapter bitrate mode in the audio_manifest sidecar.

    A single chapter file is one encoding — ``cbr`` or ``vbr`` (the per-delivery
    ``BitrateMode`` adds the rollup value ``mixed`` that only makes sense
    aggregated across chapters)."""

    CBR = "cbr"
    VBR = "vbr"


# ---------- vocab ----------


class _ShortNamed(BaseModel):
    """Common shape for riwayat / styles."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=1)
    short: str = Field(..., min_length=1, description="Used in delivery slugs")
    name: str = Field(..., min_length=1)


class Riwayah(_ShortNamed):
    pass


class Style(_ShortNamed):
    pass


class Channel(_ShortNamed):
    host_patterns: list[str] = Field(default_factory=list)
    #: Drives GH release inclusion. True for public-CDN channels whose audio
    #: can be linked from a public release manifest. ``everyayah`` may remain
    #: true for historical vocabulary compatibility, but the release adapters
    #: explicitly exclude it as an Inspector-owner-only channel.
    gh_release_eligible: bool = Field(default=False)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    url: str | None = None
    audio_categories: list[AudioCategory] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SOURCE_SLUG_RE.match(v):
            raise ValueError(f"invalid source slug: {v!r}")
        return v


class RecordingContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: Literal["studio", "broadcast", "prayer", "taraweeh", "mixed"]
    name: str = Field(..., min_length=1)


class Vocab(BaseModel):
    model_config = ConfigDict(extra="forbid")

    riwayat: list[Riwayah] = Field(default_factory=list)
    styles: list[Style] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    recording_contexts: list[RecordingContext] = Field(default_factory=list)


# ---------- reciters[] ----------


class ReciterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reciter_id: str
    name_en: str = Field(..., min_length=1)
    name_ar: str | None = None
    country: str | None = Field(default=None, description="ISO-2 or null")
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("reciter_id")
    @classmethod
    def _validate_reciter_id(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"invalid reciter_id: {v!r}")
        return v


# ---------- deliveries[] ----------


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    reciter_id: str
    riwayah: str = Field(..., min_length=1)
    style: str = Field(..., min_length=1)
    recording_context: str | None = None
    recording_year: int | None = None
    variant_label: str | None = None

    source: str = Field(..., min_length=1)
    channel: str = Field(..., min_length=1)
    #: Originating playlist/set/collection URL for download-only sources
    #: (YouTube playlist, SoundCloud set, archive item). NULL for CDN deliveries
    #: — their per-chapter URLs live in the audio_manifest sidecar. Surfaced on
    #: PublicDelivery so the reciter-detail channel cell can hyperlink to it.
    source_url: str | None = None
    audio_category: AudioCategory
    chapter_count: int = Field(..., ge=0)

    codec: str = "mp3"
    container: str = "mp3"
    sample_rate_hz: int | None = None
    channels: int | None = Field(default=None, ge=1, le=2)
    bitrate_mode: BitrateMode = BitrateMode.UNKNOWN
    bitrate_kbps_nominal: int | None = None
    total_duration_sec: int | None = None

    added_at: datetime
    added_by_hf_id: str = Field(..., min_length=1)

    @field_validator("slug", "reciter_id")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"invalid slug-form: {v!r}")
        return v

    @model_validator(mode="after")
    def _check_bitrate_invariant(self) -> Delivery:
        # bitrate_kbps_nominal must be null when bitrate_mode == mixed.
        if self.bitrate_mode == BitrateMode.MIXED and self.bitrate_kbps_nominal is not None:
            raise ValueError("bitrate_kbps_nominal must be null when bitrate_mode == mixed")
        return self


# ---------- aliases[] ----------


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["slug", "reciter_id"]
    old: str
    new: str
    ts: datetime


# ---------- derived ----------


class SourceChannelPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    channel: str
    delivery_count: int = Field(..., ge=0)


class Derived(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_channels: list[SourceChannelPair] = Field(default_factory=list)


# ---------- top-level ----------


class ReciterCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    generated_at: datetime | None = None  # stamped by the dedup build pipeline
    vocab: Vocab = Field(default_factory=Vocab)
    reciters: list[ReciterEntry] = Field(default_factory=list)
    deliveries: list[Delivery] = Field(default_factory=list)
    aliases: list[Alias] = Field(default_factory=list)
    derived: Derived = Field(default_factory=Derived)

    @model_validator(mode="after")
    def _check_unique_keys(self) -> ReciterCatalog:
        seen_reciter_ids: set[str] = set()
        for r in self.reciters:
            if r.reciter_id in seen_reciter_ids:
                raise ValueError(f"duplicate reciter_id: {r.reciter_id}")
            seen_reciter_ids.add(r.reciter_id)

        seen_delivery_slugs: set[str] = set()
        for d in self.deliveries:
            if d.slug in seen_delivery_slugs:
                raise ValueError(f"duplicate delivery slug: {d.slug}")
            seen_delivery_slugs.add(d.slug)

        # FKs: every delivery.reciter_id must reference a known reciter; every
        # delivery.{riwayah,style,source,channel} must be a known vocab slug;
        # recording_context, if set, must be a known recording_context slug.
        riwayat = {r.slug for r in self.vocab.riwayat}
        styles = {s.slug for s in self.vocab.styles}
        sources = {s.slug for s in self.vocab.sources}
        channels = {c.slug for c in self.vocab.channels}
        contexts = {c.slug for c in self.vocab.recording_contexts}

        for d in self.deliveries:
            if d.reciter_id not in seen_reciter_ids:
                raise ValueError(
                    f"delivery {d.slug!r} references unknown reciter_id {d.reciter_id!r}"
                )
            if d.riwayah not in riwayat:
                raise ValueError(f"delivery {d.slug!r}: riwayah {d.riwayah!r} not in vocab.riwayat")
            if d.style not in styles:
                raise ValueError(f"delivery {d.slug!r}: style {d.style!r} not in vocab.styles")
            if d.source not in sources:
                raise ValueError(f"delivery {d.slug!r}: source {d.source!r} not in vocab.sources")
            if d.channel not in channels:
                raise ValueError(
                    f"delivery {d.slug!r}: channel {d.channel!r} not in vocab.channels"
                )
            if d.recording_context is not None and d.recording_context not in contexts:
                raise ValueError(
                    f"delivery {d.slug!r}: recording_context "
                    f"{d.recording_context!r} not in vocab.recording_contexts"
                )

        return self

    def find_delivery(self, slug: str) -> Delivery | None:
        for d in self.deliveries:
            if d.slug == slug:
                return d
        return None

    def find_reciter(self, reciter_id: str) -> ReciterEntry | None:
        for r in self.reciters:
            if r.reciter_id == reciter_id:
                return r
        return None


# ---------------------------------------------------------------------------
# Sidecar: per-delivery audio_manifest/<slug>.json
# ---------------------------------------------------------------------------


class SidecarMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checksum: str = Field(..., min_length=1)
    source_meta_reciter: str | None = None
    source_manifest_path: str | None = None
    chapter_count: int = Field(..., ge=0)
    category: AudioCategory


class ChapterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)
    duration_sec: int | None = Field(default=None, ge=0)
    bitrate_kbps: int | None = Field(default=None, ge=0)
    bitrate_mode: ChapterBitrateMode | None = None
    max_linear_seek_err_ms: int | None = Field(default=None, ge=0)
    # Provenance for combined-file intakes (one source mp3 → several chapters).
    # ``url`` is then a unique per-chapter bucket path (so the inverse
    # URL→chapter index doesn't collapse); ``source_url`` keeps the original
    # source (e.g. the Drive/YouTube link) and ``source_offset_ms`` is where the
    # chapter begins inside that source. Both null for normal single-file
    # chapters whose ``url`` is already the source.
    source_url: str | None = Field(default=None, min_length=1)
    source_offset_ms: int | None = Field(default=None, ge=0)


class ManifestSource(BaseModel):
    """A source file whose chapters are not known yet (a playlist entry).

    The align pipeline acquires it, lets the aligner detect which surahs it
    holds, splits it into ``chapters`` and then drops it from ``sources``."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    title: str | None = None


class AudioManifestSidecar(BaseModel):
    """Schema for ``<bucket>/catalog/audio_manifest/<slug>.json``.

    One file per delivery. Carries the per-chapter URL map + (size, duration,
    bitrate) when probed. Keys in ``chapters`` are stringified surah numbers
    (``"1"``–``"114"``) for ``by_surah``, or ``"<surah>:<ayah>"`` for
    ``by_ayah``.

    Extras handling: pure ``extra="forbid"`` — any unknown field raises a
    ``ValidationError``. ``_meta`` is the on-disk key for ``meta``; resolved
    via ``alias`` + ``populate_by_name``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = 1
    slug: str
    meta: SidecarMeta = Field(alias="_meta")
    chapters: dict[str, ChapterEntry] = Field(default_factory=dict)
    #: Files still awaiting surah detection (see :class:`ManifestSource`).
    sources: list[ManifestSource] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_empty_sources(self, handler: SerializerFunctionWrapHandler) -> dict:
        # Only a playlist delivery awaiting its first align has sources; every
        # other manifest keeps its exact on-disk shape.
        data = handler(self)
        if not self.sources:
            data.pop("sources", None)
        return data

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"invalid slug: {v!r}")
        return v
