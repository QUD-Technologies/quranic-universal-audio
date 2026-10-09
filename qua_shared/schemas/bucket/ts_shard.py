"""Timestamp shards stored as ``timestamps/<chapter>.json.br``.

Two profiles share the path and the Brotli envelope, discriminated by
``_meta.profile``:

- **``native``** (schema 13, and 14 once a native document is next rebuilt) —
  the full phonemizer projection: cells, sounds, rule occurrences, animation
  tokens. Requires quranic-phonemizer, so it exists only for Hafs.
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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class TsShardReading(_Closed):
    id: str = Field(min_length=1)
    parts: list[TsShardPart]
    render: TsCompactRender
    timing: TsShardTiming

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
        return self


class TsNativeProfile(_Closed):
    riwayah: str = Field(min_length=1)
    script: str = Field(min_length=1)
    variant: dict[str, str]
    extra_phonemes: list[str]


class TsShardMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    #: 13 is every object written before the word profile existed; 14 is the
    #: current version. Both are native.
    #:
    #: There is deliberately NO ``profile`` field here. The native meta is the
    #: one the Flask shard route serves as byte-passthrough, so a defaulted
    #: field would serialize into every round-trip and change 4,126 existing
    #: objects' bytes. Native is the *absence* of ``profile``; the
    #: discriminator is read from the raw dict by
    #: ``qua_shared.timestamps_shards.shard_profile``. ``extra="allow"`` means
    #: a document that does carry ``profile: "native"`` still validates.
    schema_version: Literal[13, 14]
    chapter: int = Field(ge=1, le=114)
    audio_category: str = Field(min_length=1)
    phonemizer_version: str = Field(min_length=1)
    native_schema_version: Literal[2]
    renderer_codec_version: Literal[1]
    native_profile: TsNativeProfile


class TsShardDoc(_Closed):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: TsShardMeta = Field(alias="_meta")
    readings: list[TsShardReading]


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
    "TsShardDoc",
    "TsShardMeta",
    "TsShardPart",
    "TsShardProfile",
    "TsShardReading",
    "TsShardTiming",
    "TsSoundTiming",
    "TsAnimationTiming",
    "TsWordBoundary",
    "TsWordRow",
    "TsWordShardDoc",
    "TsWordShardMeta",
    "TsWordShardReading",
    "TsWordTiming",
]
