"""Per-delivery readings summary — ``reciters/<slug>/timestamps/readings.json``.

What a Hafs recitation reads wherever the riwayah allows more than one way, folded
from its v15 shards by ``inspector/services/reference/readings.py``. One row per
selector and word; every option of the selector with the verses read that way
(an option never read has no verses). Served as is by ``GET /api/ts/readings/<slug>``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

READINGS_SCHEMA_VERSION = 1


class TsReadingVerse(BaseModel):
    """A verse an option was read at; ``label`` spans verses when the words cross one."""

    model_config = ConfigDict(extra="forbid")

    surah: int
    ayah: int
    label: str


class TsReadingOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option: str
    verses: list[TsReadingVerse] = Field(default_factory=list)


class TsReadingRow(BaseModel):
    """One selector at one word (two for a boundary selector), in mushaf order."""

    model_config = ConfigDict(extra="forbid")

    selector: str
    key: str
    texts: list[str]
    options: list[TsReadingOption]


class TsReadingsDoc(BaseModel):
    """The readings summary; ``rows`` is empty for a non-Hafs or pre-v15 delivery."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = READINGS_SCHEMA_VERSION
    slug: str
    built_at: str = ""
    rows: list[TsReadingRow] = Field(default_factory=list)
