"""Timestamp shards stored as ``timestamps/<chapter>.json.br``.

Two profiles share the path and the Brotli envelope, discriminated by
``_meta.profile``:

- **``native``** (schema 13, 14 or 15) — the full phonemizer projection: cells,
  sounds, rule occurrences, animation tokens. Requires quranic-phonemizer, so it
  exists only for Hafs. v15 adds a reading's variant faces (``variants``) with
  the chapter's ``_meta.variant_catalogue`` and ``variant_policy``.
- **``word``** (schema 14) — word intervals and provenance only, for a riwayah
  timed through the Hafs MFA proxy. No phones, no letters, no cell geometry;
  those shapes belong to the Hafs reference script and cannot be honestly
  synthesised for another edition.

``profile`` is **absent on every existing v13 object** and reads as ``native``,
so the 37 published Hafs reciters are never restamped. Dispatch through
``qua_shared.timestamps_shards.shard_profile`` / ``parse_shard`` rather than
matching on ``schema_version`` alone.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH, SUPPORTED_RIWAYAT

#: SDK slugs a word profile may name. Hafs is excluded on purpose: it has a
#: native profile carrying cells, sounds and letter timings, so a word-profile
#: document claiming Hafs is a producer bug that would silently downgrade
#: every reader from letters to words.
_WORD_PROFILE_RIWAYAT = frozenset(SUPPORTED_RIWAYAT.values()) - {DEFAULT_SDK_RIWAYAH}

TS_SHARD_SCHEMA_VERSION = 14
TsShardProfile = Literal["native", "word"]

TsShardPart = tuple[str, int, int, int, int]
TsWordTiming = tuple[int, int]
TsSoundTiming = tuple[int, int]
TsAnimationTiming = tuple[int | None, int | None]
TsColumnTiming = tuple[str | int, int | None, int | None]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _OmitsAbsentV15(BaseModel):
    """Dumps without the v15 fields a document does not carry, so a v13/v14
    document round-trips to its own bytes."""

    v15_fields: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _omit_absent(self, handler):
        data = handler(self)
        for name in self.v15_fields:
            if getattr(self, name) is None:
                data.pop(name, None)
        return data


class TsCompactRender(_Closed):
    v: Literal[1]
    m: tuple[str, str, str]
    p: list[str]
    r: list[str]
    w: list[list[Any]]
    b: list[list[Any]]
    a: list[list[Any]]

    @model_validator(mode="after")
    def _counts(self):
        if len(self.w) != len(self.b):
            raise ValueError("compact word and boundary counts differ")
        return self


class TsShardTiming(_Closed):
    w: list[TsWordTiming]
    s: list[TsSoundTiming]
    a: list[TsAnimationTiming]
    c: list[TsColumnTiming]

    @model_validator(mode="after")
    def _ordered(self):
        for label, rows in (("word", self.w), ("sound", self.s)):
            if any(end < start for start, end in rows):
                raise ValueError(f"{label} timing end precedes start")
        for start, end in self.a:
            if (start is None) != (end is None):
                raise ValueError("animation timing has a half-null interval")
            if start is not None and end is not None and end < start:
                raise ValueError("animation timing end precedes start")
        for _, start, end in self.c:
            if (start is None) != (end is None):
                raise ValueError("column timing has a half-null interval")
            if start is not None and end is not None and end < start:
                raise ValueError("column timing end precedes start")
        return self


class TsVariantCells(_Closed):
    """Rendered-reading cells another option changes: column, sound and boundary ids."""

    c: list[int]
    s: list[int]
    b: list[int]


class TsReadingVariant(_Closed):
    """One shown variant occurrence. ``words``/``targets`` are reading word ids,
    ``boundary`` a native boundary id (from 1); ``affected`` has one entry per
    option not chosen, in the rendered reading's ids."""

    id: str = Field(min_length=1)
    chosen: str = Field(min_length=1)
    words: list[int] = Field(min_length=1, max_length=2)
    targets: list[int] = Field(min_length=1)
    anchor: Literal["word", "boundary"]
    boundary: int | None
    by: Literal["scored", "tie", "length", "majority", "pause", "default"]
    score: float | None
    affected: dict[str, TsVariantCells]

    @model_validator(mode="after")
    def _anchor(self):
        if (self.anchor == "boundary") != (self.boundary is not None):
            raise ValueError("variant boundary must be set exactly for a boundary anchor")
        if self.chosen in self.affected:
            raise ValueError("variant affected lists its chosen option")
        return self


class TsVariantDefinition(_Closed):
    name: str = Field(min_length=1)
    description: str | None
    options: list[str] = Field(min_length=2)
    default: str

    @model_validator(mode="after")
    def _default(self):
        if self.default not in self.options:
            raise ValueError("variant default is not an option")
        return self


class TsShardReading(_OmitsAbsentV15, _Closed):
    v15_fields: ClassVar[tuple[str, ...]] = ("variants",)

    id: str = Field(min_length=1)
    parts: list[TsShardPart]
    render: TsCompactRender
    timing: TsShardTiming
    variants: list[TsReadingVariant] | None = None

    @model_validator(mode="after")
    def _closure(self):
        if len(self.timing.w) != len(self.render.w):
            raise ValueError("word timing count differs from compact words")
        if len(self.timing.s) != len(self.render.p):
            raise ValueError("sound timing count differs from compact tokens")
        if len(self.timing.a) != len(self.render.a):
            raise ValueError("animation timing count differs from animation tokens")
        for ref, start, end, first, count in self.parts:
            if not ref or end < start or first < 0 or count < 1:
                raise ValueError("invalid compact part")
            if first + count > len(self.render.w):
                raise ValueError("compact part references unknown words")
        for variant in self.variants or ():
            self._variant_ranges(variant)
        return self

    def _variant_ranges(self, variant: TsReadingVariant) -> None:
        words, sounds = range(len(self.render.w)), range(len(self.render.p))
        boundaries = range(1, len(self.render.b) + 1)
        if not set(variant.words) | set(variant.targets) <= set(words):
            raise ValueError(f"variant {variant.id} references unknown words")
        if variant.boundary is not None and variant.boundary not in boundaries:
            raise ValueError(f"variant {variant.id} anchors an unknown boundary")
        for cells in variant.affected.values():
            if not set(cells.s) <= set(sounds) or not set(cells.b) <= set(boundaries):
                raise ValueError(f"variant {variant.id} affects unknown sounds or boundaries")


class TsNativeProfile(_Closed):
    riwayah: str = Field(min_length=1)
    script: str = Field(min_length=1)
    variant: dict[str, str]
    extra_phonemes: list[str]


class TsShardMeta(_OmitsAbsentV15, BaseModel):
    model_config = ConfigDict(extra="allow")
    v15_fields: ClassVar[tuple[str, ...]] = ("variant_catalogue", "variant_policy")

    #: 13 is every object written before the word profile existed; 14 followed
    #: it and 15 adds reading variants. All are native.
    #:
    #: There is deliberately NO ``profile`` field here. The native meta is the
    #: one the Flask shard route serves as byte-passthrough, so a defaulted
    #: field would serialize into every round-trip and change 4,126 existing
    #: objects' bytes. Native is the *absence* of ``profile``; the
    #: discriminator is read from the raw dict by
    #: ``qua_shared.timestamps_shards.shard_profile``. ``extra="allow"`` means
    #: a document that does carry ``profile: "native"`` still validates.
    schema_version: Literal[13, 14, 15]
    chapter: int = Field(ge=1, le=114)
    audio_category: str = Field(min_length=1)
    phonemizer_version: str = Field(min_length=1)
    native_schema_version: Literal[2]
    renderer_codec_version: Literal[1]
    native_profile: TsNativeProfile
    variant_catalogue: dict[str, TsVariantDefinition] | None = None
    variant_policy: str | None = None


class TsShardDoc(_Closed):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: TsShardMeta = Field(alias="_meta")
    readings: list[TsShardReading]

    @model_validator(mode="after")
    def _variants(self):
        catalogue = self.meta.variant_catalogue or {}
        shown = [v for reading in self.readings for v in reading.variants or ()]
        if (shown or catalogue) and self.meta.schema_version < 15:
            raise ValueError("reading variants need schema v15")
        for variant in shown:
            spec = catalogue.get(variant.id)
            if spec is None:
                raise ValueError(f"variant {variant.id} is not in the chapter catalogue")
            others = set(spec.options) - {variant.chosen}
            if variant.chosen not in spec.options or set(variant.affected) != others:
                raise ValueError(f"variant {variant.id} names unknown options")
        return self


# ---------------------------------------------------------------------------
# Word profile (schema 14) — proxy-timed, word intervals only.
# ---------------------------------------------------------------------------

#: ``(target_ref, exact_target_text, start_ms, end_ms)``. ``target_ref`` is the
#: delivery edition's coordinate, or ``0:0:N`` for an unnumbered special (the
#: opening Basmala / Isti'adha ordinals, per the SDK timing contract).
TsWordRow = tuple[str, str, int, int]

#: ``(state_code, verse_end)`` — one per word. ``state_code`` indexes
#: :data:`TS_WORD_BOUNDARY_STATES`; ``verse_end`` is the ayah number that ends
#: at this boundary, else ``None``. Same two facts v13 carries in
#: ``render.b[i][0]`` and ``render.b[i][4]``.
TsWordBoundary = tuple[int, int | None]

TS_WORD_BOUNDARY_STATES: tuple[str, ...] = ("start", "join", "sakt", "stop")


class TsWordShardMeta(BaseModel):
    #: ``extra="allow"`` matches the native meta's documented forward-compat
    #: exception — the producer may add provenance the readers ignore.
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[14]
    profile: Literal["word"]
    chapter: int = Field(ge=1, le=114)
    audio_category: str = Field(min_length=1)

    #: SDK slug (``warsh`` / ``qalun`` / ``shuba``) — the edition these
    #: coordinates and this text belong to.
    riwayah: str = Field(min_length=1)
    edition_id: str = Field(min_length=1)

    @field_validator("riwayah")
    @classmethod
    def _a_non_hafs_edition(cls, value: str) -> str:
        if value not in _WORD_PROFILE_RIWAYAT:
            raise ValueError(
                f"word-profile riwayah must be one of "
                f"{sorted(_WORD_PROFILE_RIWAYAT)}, got {value!r}"
            )
        return value

    @field_validator("reference_riwayah")
    @classmethod
    def _a_known_reference(cls, value: str) -> str:
        if value not in SUPPORTED_RIWAYAT.values():
            raise ValueError(f"unknown reference riwayah {value!r}")
        return value

    #: Digest of the edition word index the text was taken from; the audit
    #: refuses a shard whose text came from a different index revision.
    words_sha256: str = Field(min_length=64, max_length=64)

    #: How the intervals were obtained. Only one provider exists: MFA against
    #: the Hafs acoustic model using Hafs proxy phones. Recorded so a consumer
    #: can tell a proxy timing from a future native one without guessing.
    timing_provider: Literal["hafs_proxy_mfa", "hafs_proxy_neural"]
    reference_riwayah: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    #: The static Hafs->target map applied, or ``None`` for an identity result.
    projection_id: str | None = None
    projection_sha256: str | None = None


class TsWordShardReading(_Closed):
    id: str = Field(min_length=1)
    parts: list[TsShardPart]
    words: list[TsWordRow]
    boundaries: list[TsWordBoundary]

    @model_validator(mode="after")
    def _closure(self):
        if len(self.boundaries) != len(self.words):
            raise ValueError("word and boundary counts differ")
        for _, _, start, end in self.words:
            if end < start:
                raise ValueError("word timing end precedes start")
        for state, verse_end in self.boundaries:
            if not 0 <= state < len(TS_WORD_BOUNDARY_STATES):
                raise ValueError(f"unknown word boundary state code {state}")
            if verse_end is not None and verse_end < 1:
                raise ValueError("verse_end must be a positive ayah number")
        for ref, start, end, first, count in self.parts:
            if not ref or end < start or first < 0 or count < 1:
                raise ValueError("invalid word part")
            if first + count > len(self.words):
                raise ValueError("word part references unknown words")
        return self


class TsWordShardDoc(_Closed):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: TsWordShardMeta = Field(alias="_meta")
    readings: list[TsWordShardReading]


__all__ = [
    "TS_SHARD_SCHEMA_VERSION",
    "TS_WORD_BOUNDARY_STATES",
    "TsColumnTiming",
    "TsCompactRender",
    "TsNativeProfile",
    "TsReadingVariant",
    "TsShardDoc",
    "TsShardMeta",
    "TsShardPart",
    "TsShardProfile",
    "TsShardReading",
    "TsShardTiming",
    "TsSoundTiming",
    "TsAnimationTiming",
    "TsVariantCells",
    "TsVariantDefinition",
    "TsWordBoundary",
    "TsWordRow",
    "TsWordShardDoc",
    "TsWordShardMeta",
    "TsWordShardReading",
    "TsWordTiming",
]
