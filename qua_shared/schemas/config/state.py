"""Reciter state file schema: ``<bucket>/state/reciter_state.json``.

Source of truth for reciter lifecycle + assignee. Inspector backend is the
sole writer.

Spec: docs/planning/inspector-deploy/v2/inspector-state-management.md §2.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Slug regex: lowercase letter prefix, ASCII alnum + underscore, 2-80 chars.
# Matches docs/reference/catalog.md §3 (extended ceiling to 80 to
# accommodate disambiguators like _byayah, _128k_v2).
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


class Visibility(str, Enum):
    PUBLIC = "public"
    DISCARDED = "discarded"
    # ARCHIVED is deferred — do not add until the archive lifecycle ships.


class ReciterState(str, Enum):
    CATALOGUED = "catalogued"
    AWAITING_ALIGNMENT = "awaiting_alignment"
    AWAITING_REVIEW = "awaiting_review"
    UNDER_REVIEW = "under_review"
    RELEASED = "released"
    # NOTE: ``awaiting_timestamps`` was removed — publishing now goes straight
    # ``under_review → released`` when the timestamps job succeeds (the reciter
    # stays under_review while the job runs). See ``_h_published`` and
    # ``services.admin.timestamps_jobs.complete_timestamps_job``.


class QualityStatus(str, Enum):
    """Maintainer's manual verdict on how clean a delivery's recitation is.

    A human judgement made after looking at the flags and validation results,
    not a computed score. Orthogonal to coverage (full/partial).
    """

    CLEAN = "clean"  # nothing wrong
    ALMOST_CLEAN = "almost_clean"  # reciter slips / small fixes; nothing missing
    NEEDS_WORK = "needs_work"  # missing content or issues everywhere; not app-ready


class RevisionContext(BaseModel):
    """Set by ``admin.unlocked_for_revision``; cleared on re-publish.

    Lets the publish endpoint auto-restore the row to its prior state and
    re-credit the original assignee (for a "your contributions" surface).
    """

    model_config = ConfigDict(extra="forbid")

    unlocked_from_state: Literal["released"]
    unlocked_at: datetime
    unlocked_by_hf_id: str = Field(..., min_length=1)
    original_assignee_hf_id: str | None = None


class ReciterRow(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    slug: str
    state: ReciterState
    state_since: datetime

    assignee_hf_id: str | None = None
    assignee_login: str | None = None
    assignee_since: datetime | None = None

    marked_ready: bool = False
    visibility: Visibility = Visibility.PUBLIC
    visibility_reason: str | None = None

    last_save_at: datetime | None = None
    timestamps_job_ids: list[str] = Field(default_factory=list)
    revision_in_progress: RevisionContext | None = None
    quality_status: QualityStatus | None = None

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"invalid slug: {v!r} does not match {SLUG_RE.pattern}")
        return v

    @model_validator(mode="after")
    def _check_state_invariants(self) -> ReciterRow:
        # Per-state required-field invariants per state-mgmt §4 (Editable column).
        state = self.state
        has_assignee = self.assignee_hf_id is not None

        if state == ReciterState.UNDER_REVIEW:
            if not has_assignee:
                raise ValueError("under_review row must have assignee_hf_id")
            if self.assignee_since is None:
                raise ValueError("under_review row must have assignee_since")
        else:
            # No other state may carry an assignee.
            if has_assignee:
                raise ValueError(f"state={state.value!r} must not have assignee_hf_id")
            if self.assignee_login is not None or self.assignee_since is not None:
                raise ValueError(
                    f"state={state.value!r} must not have assignee_login/assignee_since"
                )

        # marked_ready is only legal on under_review rows that have an assignee.
        if self.marked_ready and not (state == ReciterState.UNDER_REVIEW and has_assignee):
            raise ValueError("marked_ready requires under_review + assignee_hf_id")

        # revision_in_progress is set by admin.unlocked_for_revision on
        # awaiting_review and must survive the revision window — a claim moves
        # the row to under_review (keeping the breadcrumb) and reciter.published
        # clears it on the way to awaiting_timestamps. So it is legal on
        # awaiting_review + under_review, and must be absent everywhere else.
        if self.revision_in_progress is not None and state not in (
            ReciterState.AWAITING_REVIEW,
            ReciterState.UNDER_REVIEW,
        ):
            raise ValueError(
                "revision_in_progress only valid on awaiting_review/under_review "
                f"(got {state.value!r})"
            )

        # If assignee_login is set, assignee_hf_id must also be set.
        if self.assignee_login is not None and self.assignee_hf_id is None:
            raise ValueError("assignee_login set without assignee_hf_id")

        return self


class ReciterStateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    writer_version: str = "inspector-v2"
    reciters: list[ReciterRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_slugs(self) -> ReciterStateFile:
        seen: set[str] = set()
        for r in self.reciters:
            if r.slug in seen:
                raise ValueError(f"duplicate slug in state file: {r.slug}")
            seen.add(r.slug)
        return self

    def find(self, slug: str) -> ReciterRow | None:
        for r in self.reciters:
            if r.slug == slug:
                return r
        return None

    def by_slug(self) -> dict[str, ReciterRow]:
        """Materialize a slug→row dict (for in-memory ``state_store``)."""
        return {r.slug: r for r in self.reciters}
