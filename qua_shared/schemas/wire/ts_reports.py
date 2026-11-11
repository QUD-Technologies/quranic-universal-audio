"""Timestamps-tab report wire shapes.

Backs the categorized, cell-addressable Report flow on the Timestamps tab. A
report names a ``category`` with an optional per-category ``subtype`` and points
at a native v13 shard target. Targets carry a reading id plus a native entity id;
renderer positions are never persisted.

Served by ``inspector/routes/timestamps/reports.py``:
- ``GET    /api/ts/<slug>/reports``               → ``TsReciterReports`` (per-verse counts)
- ``GET    /api/ts/<slug>/reports/<verse_key>``   → ``TsVerseReports`` (a verse's reports)
- ``POST   /api/ts/<slug>/reports``               ← ``TsReportCreateRequest`` → ``TsReport``
- ``POST   /api/ts/<slug>/reports/batch``         ← ``TsReportBatchCreateRequest`` → ``TsReportBatchResult``
- ``POST   /api/ts/<slug>/reports/<id>/resolve``  ← ``TsReportResolveRequest`` → ``TsReport``
- ``POST   /api/ts/<slug>/reports/<verse>/reading/<reading>/word/<word>/<cat>/resolve`` ← ``TsReportResolveRequest`` → ``TsVerseReports``
- ``DELETE /api/ts/<slug>/reports/<id>``

``author`` on a report is populated only when the caller holds
``timestamps.see_reporter_identity`` (owner-default); everyone else sees the
report but no author. ``mine`` marks the caller's own report (matched by
``hf_user_id`` when signed in, else by the ``anon_token`` query param). ``stale``
is set when a timestamp regeneration changed the targeted content.

Per-category rules (enforced by ``TsReportCreateRequest`` validators):
- ``audio``   — comment mandatory; target verse|word.
- ``timing``  — two boundary axes ``onset``/``offset`` (each early|late, ≥1 set),
                no subtype; comment optional; target native timed entities.
                The human label is derived via ``timing_label``.
- ``tajweed`` — subtype wrong_rule|missing_rule;
                comment mandatory; target column|sound|group|bridge.
- ``silence`` — targets a native boundary. Subtype pause_boundary
                |pause_wasl|pause_missed; selection-only (no comment); ``pause_boundary``
                carries the ``onset``/``offset`` axes (the other two are binary). Public.
- ``other``   — no subtype; comment mandatory; any target.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ReportCategory = Literal["audio", "timing", "tajweed", "phonemes", "silence", "other"]
TargetKind = Literal["verse", "word", "column", "sound", "group", "boundary", "bridge"]

#: Per-category subtype — tajweed + silence. The owning category constrains which
#: values are valid (see ``TsReportCreateRequest._check``). Timing does NOT use
#: a subtype; it carries the ``onset``/``offset`` boundary axes instead.
ReportSubtype = Literal[
    "wrong_rule",
    "missing_rule",
    "pause_boundary",
    "pause_wasl",
    "pause_missed",
]

#: A timing boundary's error direction. A timing (or silence ``pause_boundary``)
#: report sets ``onset`` and/or ``offset`` (≥1); ``None`` on an axis = that boundary
#: is fine.
TimingDir = Literal["early", "late"]

_TAJWEED_SUBTYPES = frozenset({"wrong_rule", "missing_rule"})
#: silence subtypes — a wrong-boundary pause (dual-axis), a pause that shouldn't
#: exist (should be waṣl), and a missing pause. All target a native boundary.
_SILENCE_SUBTYPES = frozenset({"pause_boundary", "pause_wasl", "pause_missed"})
#: target_kind values allowed per category.
_ALLOWED_KINDS: dict[str, frozenset[str]] = {
    "audio": frozenset({"verse", "word"}),
    "timing": frozenset({"word", "column", "sound", "group", "boundary", "bridge"}),
    "tajweed": frozenset({"column", "sound", "group", "bridge"}),
    "phonemes": frozenset({"sound", "bridge"}),
    "silence": frozenset({"boundary"}),
    "other": frozenset(TargetKind.__args__),
}
#: categories whose comment is always mandatory.
_COMMENT_REQUIRED = frozenset({"audio", "tajweed", "other"})


def _verse_key_ok(verse_key: str) -> bool:
    parts = verse_key.split(":")
    return len(parts) == 2 and all(p.isdigit() and 1 <= len(p) <= 3 for p in parts)


def _validate_report_item(
    category: str,
    subtype: str | None,
    target: TsReportTarget,
    comment: str | None,
    selected_rule_tags: list[str] | None,
    onset: str | None = None,
    offset: str | None = None,
) -> None:
    """Per-item rules shared by single + batch create (keeps them from drifting):
    classification↔category, target-kind↔category, mandatory comment, and the
    ``selected_rule_tags`` gate. Raises ``ValueError``.

    Timing carries the ``onset``/``offset`` boundary axes (≥1 set) and no
    ``subtype``; tajweed + silence carry a ``subtype``; silence ``pause_boundary``
    also carries the axes; audio/other carry neither.
    """
    if category == "timing":
        if subtype is not None:
            raise ValueError("timing reports use onset/offset, not subtype")
        if onset is None and offset is None:
            raise ValueError("timing reports require at least one of onset/offset")
    elif category == "tajweed":
        if subtype not in _TAJWEED_SUBTYPES:
            raise ValueError("tajweed reports require subtype wrong_rule|missing_rule")
        if onset is not None or offset is not None:
            raise ValueError("onset/offset are timing-only")
    elif category == "silence":
        if subtype not in _SILENCE_SUBTYPES:
            raise ValueError(
                "silence reports require subtype pause_boundary|pause_wasl|pause_missed"
            )
        if subtype == "pause_boundary":
            if onset is None and offset is None:
                raise ValueError("pause_boundary reports require at least one of onset/offset")
        elif onset is not None or offset is not None:
            raise ValueError("onset/offset are only for pause_boundary silence reports")
    else:
        if subtype is not None:
            raise ValueError(f"{category} reports take no subtype")
        if onset is not None or offset is not None:
            raise ValueError("onset/offset are timing-only")
    if target.kind not in _ALLOWED_KINDS[category]:
        raise ValueError(
            f"{category} reports cannot target {target.kind!r}; "
            f"allowed: {sorted(_ALLOWED_KINDS[category])}"
        )
    if category in _COMMENT_REQUIRED and not comment:
        raise ValueError(f"{category} reports require a comment")
    if selected_rule_tags and not (category == "tajweed" and subtype == "wrong_rule"):
        raise ValueError("selected_rule_tags is only valid on tajweed wrong_rule reports")


def timing_label(onset: str | None, offset: str | None) -> str:
    """Human label for a timing report's boundary axes (the report matrix).

    onset/offset each ``early`` | ``late`` | ``None`` (fine). Both-None is not a
    valid timing report (the validator rejects it) and maps to ``"Timing"`` here
    defensively. Single source of truth for the derived label — BE notification
    copy and the FE both call this rather than hardcoding."""
    both: dict[tuple[str | None, str | None], str] = {
        ("early", "late"): "Too long",
        ("late", "early"): "Too short",
        ("early", "early"): "Shifted earlier",
        ("late", "late"): "Shifted later",
    }
    pair_label = both.get((onset, offset))
    if pair_label is not None:
        return pair_label
    if onset and offset is None:
        return "Starts early" if onset == "early" else "Starts late"
    if offset and onset is None:
        return "Finishes early" if offset == "early" else "Finishes late"
    return "Timing"


class TsReportTarget(BaseModel):
    """Stable native entity identity within a v12 connected reading."""

    model_config = ConfigDict(extra="forbid")

    reading_id: str = Field(min_length=1)
    kind: TargetKind
    target_id: str = Field(min_length=1)


class TsReportTimingSnapshot(BaseModel):
    """Absolute audio interval of the native target when the report was filed."""

    model_config = ConfigDict(extra="forbid")

    start_ms: int | None = None
    end_ms: int | None = None


class TsReportSnapshot(BaseModel):
    """The entity plus timing fingerprint captured at report creation."""

    model_config = ConfigDict(extra="forbid")

    #: ``None`` on a word profile, which carries no native analysis at all — a
    #: reader must not expect cells behind the fingerprint.
    native_schema_version: Literal[2] | None = 2
    shard_schema_version: Literal[12, 13, 14, 15] = 13
    #: Which shard shape the fingerprint came from. Absent on every row written
    #: before the word profile existed, all of which are native.
    shard_profile: Literal["native", "word"] = "native"
    native: dict[str, object] = Field(default_factory=dict)
    timing: TsReportTimingSnapshot | None = None


class TsReportAuthor(BaseModel):
    """Report author identity — only disclosed to identity-capable callers."""

    model_config = ConfigDict(extra="forbid")

    hf_user_id: str | None = None
    login: str | None = None
    role: str | None = None


class TsReport(BaseModel):
    """One report (verse list item / POST echo)."""

    model_config = ConfigDict(extra="forbid")

    id: int
    verse_key: str
    category: ReportCategory
    subtype: ReportSubtype | None = None
    #: Timing boundary axes (timing only); ``None`` on an axis = that boundary is fine.
    onset: TimingDir | None = None
    offset: TimingDir | None = None
    target: TsReportTarget
    snapshot: TsReportSnapshot | None = None
    comment: str | None = None
    #: Internal tajweed tag id(s) the reporter marked wrong (wrong_rule only).
    selected_rule_tags: list[str] = Field(default_factory=list)
    status: Literal["open", "resolved"]
    stale: bool = False
    resolver_comment: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    #: True when this is the caller's own report (deletable).
    mine: bool = False
    #: Author identity, present only when the caller can see reporter identity.
    author: TsReportAuthor | None = None


class TsVerseReports(BaseModel):
    """All reports on a single verse (``GET .../reports/<verse_key>``)."""

    model_config = ConfigDict(extra="forbid")

    verse_key: str
    reports: list[TsReport] = Field(default_factory=list)


class TsReportVerseCount(BaseModel):
    """A reported verse + its open / resolved counts (accordion pill)."""

    model_config = ConfigDict(extra="forbid")

    verse_key: str
    open_count: int
    resolved_count: int


class TsReciterReports(BaseModel):
    """Every reported verse for a reciter (``GET .../reports``)."""

    model_config = ConfigDict(extra="forbid")

    reports: list[TsReportVerseCount] = Field(default_factory=list)


class TsReportCreateRequest(BaseModel):
    """Create the caller's report on a verse (``POST .../reports``)."""

    model_config = ConfigDict(extra="forbid")

    verse_key: str
    category: ReportCategory
    subtype: ReportSubtype | None = None
    #: Timing boundary axes (timing only; ≥1 set). ``None`` = that boundary is fine.
    onset: TimingDir | None = None
    offset: TimingDir | None = None
    target: TsReportTarget
    comment: str | None = None
    #: Internal tajweed tag id(s) marked wrong (wrong_rule only).
    selected_rule_tags: list[str] = Field(default_factory=list)
    #: Anonymous browser token (omitted/ignored when the caller is signed in).
    anon_token: str | None = None

    @field_validator("verse_key")
    @classmethod
    def _verse_key(cls, v: str) -> str:
        if not _verse_key_ok(v):
            raise ValueError("verse_key must be 'surah:ayah' (e.g. '2:45')")
        return v

    @field_validator("comment")
    @classmethod
    def _trim_comment(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @model_validator(mode="after")
    def _check(self) -> TsReportCreateRequest:
        _validate_report_item(
            self.category,
            self.subtype,
            self.target,
            self.comment,
            self.selected_rule_tags,
            self.onset,
            self.offset,
        )
        return self


class TsReportResolveRequest(BaseModel):
    """Resolve a report (``POST .../reports/<id>/resolve``, or a timing
    word-group via its reading and native word id). Owner-gated."""

    model_config = ConfigDict(extra="forbid")

    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def _trim_comment(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None


class TsReportBatchItem(BaseModel):
    """One staged cell-annotation in a batch submit. Verse + identity are
    batch-level, so an item carries only its own category/subtype/target/comment
    (+ ``selected_rule_tags`` for tajweed wrong_rule). Same per-category rules as
    a single create (shared ``_validate_report_item``)."""

    model_config = ConfigDict(extra="forbid")

    category: ReportCategory
    subtype: ReportSubtype | None = None
    #: Timing boundary axes (timing only; ≥1 set). ``None`` = that boundary is fine.
    onset: TimingDir | None = None
    offset: TimingDir | None = None
    target: TsReportTarget
    comment: str | None = None
    selected_rule_tags: list[str] = Field(default_factory=list)

    @field_validator("comment")
    @classmethod
    def _trim_comment(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip() or None

    @model_validator(mode="after")
    def _check(self) -> TsReportBatchItem:
        _validate_report_item(
            self.category,
            self.subtype,
            self.target,
            self.comment,
            self.selected_rule_tags,
            self.onset,
            self.offset,
        )
        return self


class TsReportBatchCreateRequest(BaseModel):
    """Submit many staged annotations on ONE verse in a single transaction
    (``POST .../reports/batch``). Items may mix categories (timing + tajweed)."""

    model_config = ConfigDict(extra="forbid")

    verse_key: str
    items: list[TsReportBatchItem] = Field(min_length=1, max_length=200)
    #: Anonymous browser token (ignored when the caller is signed in).
    anon_token: str | None = None

    @field_validator("verse_key")
    @classmethod
    def _verse_key(cls, v: str) -> str:
        if not _verse_key_ok(v):
            raise ValueError("verse_key must be 'surah:ayah' (e.g. '2:45')")
        return v


class TsReportBatchResult(BaseModel):
    """Echo of a batch submit: the created/updated reports in input order, plus
    insert vs upsert counts."""

    model_config = ConfigDict(extra="forbid")

    verse_key: str
    reports: list[TsReport] = Field(default_factory=list)
    created_count: int
    updated_count: int
