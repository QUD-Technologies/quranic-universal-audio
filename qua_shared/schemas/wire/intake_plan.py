"""Wire shapes for the online intake plan (``/api/admin/intake/<id>/plan|align``).

A slugless intake (``existing_reciter_new_combo`` / ``new_reciter``) carries an
audio *source* — per-chapter links or one playlist URL (YouTube, Google Drive
folder, SoundCloud set, archive.org item, …). Before it can be minted and
aligned, the source is **enumerated** into media entries and each entry is
**matched** to the chapter(s) it holds, by title. The owner reviews that plan in
the Requests tab, fixes what the matcher could not decide, fills the catalog
identity (slug, reciter id, channel, source), then clicks Align.

The plan lives on the request row's ``payload.plan`` — it is request state, not
catalog state, until the mint. One entry may hold several consecutive chapters
(a *combined* file: ``سورتا الفاتحة والبقرة``, a juz'); the align pipeline
aligns it once and splits it per chapter.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .align_runs import AlignDevice

PlanHost = Literal["links", "youtube", "drive", "soundcloud", "archive", "other"]
PlanStatus = Literal["enumerating", "ready", "failed"]
#: How an entry's chapters were assigned. ``exact``/``high`` come from title
#: matching; ``low`` needs an owner's eye; ``manual`` is an owner edit; ``none``
#: means the entry is left out of the delivery (an intro, a du'a, a duplicate).
MatchConfidence = Literal["exact", "high", "low", "manual", "none"]


class PlanEntry(BaseModel):
    """One fetchable media file of the source and the chapters it holds."""

    model_config = ConfigDict(extra="forbid")

    key: str
    url: str
    title: str = ""
    #: 1-based position in the playlist / folder listing (``None`` for links).
    index: int | None = None
    duration_sec: float | None = None
    #: Ascending, consecutive chapters. Empty = not part of the delivery.
    chapters: list[int] = Field(default_factory=list)
    confidence: MatchConfidence = "none"


class PlanIdentity(BaseModel):
    """The catalog identity the mint writes. Proposed on enumerate, owner-edited."""

    model_config = ConfigDict(extra="forbid")

    slug: str = ""
    reciter_id: str = ""
    name_en: str | None = None
    name_ar: str | None = None
    country: str | None = None
    channel: str = ""
    #: An existing ``sources.slug`` — or a new one described by ``new_source_*``.
    source: str = ""
    new_source_name: str | None = None
    new_source_url: str | None = None
    recording_year: int | None = Field(default=None, ge=600, le=2200)


class PlanCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[int] = Field(default_factory=list)
    missing: list[int] = Field(default_factory=list)
    duplicates: list[int] = Field(default_factory=list)
    combined_entries: int = 0


class IntakePlan(BaseModel):
    """``payload.plan`` of an intake request row."""

    model_config = ConfigDict(extra="forbid")

    status: PlanStatus
    error: str | None = None
    host: PlanHost = "other"
    source_url: str | None = None
    #: Uploader / channel of the playlist when the host reports one (YouTube).
    uploader: str | None = None
    uploader_url: str | None = None
    entries: list[PlanEntry] = Field(default_factory=list)
    identity: PlanIdentity = Field(default_factory=PlanIdentity)
    created_at: str
    updated_at: str


class PlanOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str


class IntakePlanView(IntakePlan):
    """``GET/POST/PUT /api/admin/intake/<id>/plan`` response: the plan plus the
    live check the Align button gates on and the catalog vocab to pick from."""

    coverage: PlanCoverage = Field(default_factory=PlanCoverage)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    channel_options: list[PlanOption] = Field(default_factory=list)
    source_options: list[PlanOption] = Field(default_factory=list)


class PlanEntryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    chapters: list[int] = Field(default_factory=list)


class IntakePlanUpdate(BaseModel):
    """Body of ``PUT /api/admin/intake/<id>/plan`` — the owner's review."""

    model_config = ConfigDict(extra="forbid")

    entries: list[PlanEntryEdit] = Field(default_factory=list)
    identity: PlanIdentity


class IntakeAlignRequest(BaseModel):
    """Body of ``POST /api/admin/intake/<id>/align``."""

    model_config = ConfigDict(extra="forbid")

    device: AlignDevice = "GPU"


class IntakeAlignResponse(BaseModel):
    """The mint always lands before the run starts; a refused start (budget,
    config) is reported, not raised — Align on the new slug row retries it."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    state: str | None = None
    align_started: bool = False
    align_error: str | None = None
