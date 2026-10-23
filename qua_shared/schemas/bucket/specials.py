"""Per-reciter ``specials.json`` schema — timed non-verse segments around a chapter.

Lives at ``reciters/<slug>/specials.json``. Records every special the
pipeline ever saw at a chapter's edges (Isti'adha / Basmala before ayah 1,
Amin / Sadaqa / prayer transitions after the last ayah), whether or not the
row was later stripped from ``detailed.json``. Stripping is a presentation
choice for the verse data; the special's timing is kept here.

Times are milliseconds on the chapter's bucket audio timeline — the same base
as ``detailed.json`` and the timestamp shards. A release maps them to the
source file with ``chapter_offsets_ms`` exactly as it does verse times.

Only openers (Isti'adha / Basmala) are released; ``qua_shared.specials``
projects this document into the public ``ReleaseSpecials`` shape. Provenance
(``source``), confidence and the voice score never leave the bucket.

Extras handling: pure ``extra="forbid"``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPECIALS_SCHEMA_VERSION = 1

SpecialType = Literal["Isti'adha", "Basmala", "Amin", "Sadaqa", "Takbir", "Tahmeed", "Tasleem"]
OPENER_TYPES: tuple[SpecialType, ...] = ("Isti'adha", "Basmala")

#: Where a special was recovered from. ``align`` = written by the align
#: pipeline at promote time; ``fatiha_verse`` = Al-Fatiha's Basmala in an
#: edition that numbers it as verse 1:1 (it stays a verse AND is listed here);
#: the rest are backfill provenance.
SpecialSource = Literal[
    "align", "fatiha_verse", "pipeline_strip", "user_delete", "in_detailed", "aligner_backfill"
]

#: ``same`` / ``different`` are automatic; ``needs_confirmation`` waits for a
#: maintainer, whose answer is recorded in ``voice_confirmed``.
VoiceVerdict = Literal["same", "different", "needs_confirmation"]

Position = Literal["pre", "post"]


class SpecialEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SpecialType
    position: Position = "pre"
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    #: Per-word ``[start_ms, end_ms]``, chapter-absolute. Empty until a timing
    #: pass has timed the special.
    words: list[tuple[int, int]] = Field(default_factory=list)
    confidence: float | None = None
    source: SpecialSource
    #: The recorded end ran into ayah 1 (a swallowed muqattaʿ) and was clipped.
    clipped: bool = False
    #: No-model voice z-score of this clip against the reciter's own verse audio.
    voice_z: float | None = None
    voice_verdict: VoiceVerdict | None = None
    #: A maintainer's answer to ``needs_confirmation``: True = the reciter's voice.
    voice_confirmed: bool | None = None

    @model_validator(mode="after")
    def _span(self) -> SpecialEntry:
        if self.end_ms <= self.start_ms:
            raise ValueError(f"special {self.type} has an empty span {self.start_ms}-{self.end_ms}")
        return self

    @property
    def same_voice(self) -> bool | None:
        """The released answer: a maintainer's confirmation wins over the automatic verdict."""
        if self.voice_confirmed is not None:
            return self.voice_confirmed
        if self.voice_verdict == "same":
            return True
        if self.voice_verdict == "different":
            return False
        return None


class SpecialsDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SPECIALS_SCHEMA_VERSION
    generated_at: str  # ISO-8601 UTC
    #: Chapter number (as a string key) → specials in time order.
    chapters: dict[str, list[SpecialEntry]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ordered(self) -> SpecialsDoc:
        for ch, items in self.chapters.items():
            if not ch.isdigit() or not 1 <= int(ch) <= 114:
                raise ValueError(f"chapter key {ch!r} is not 1..114")
            starts = [s.start_ms for s in items]
            if starts != sorted(starts):
                raise ValueError(f"chapter {ch} specials are not in time order")
        return self
