"""Admin-dashboard Requests-tab wire schemas.

Read model served by ``GET /api/admin/requests``. Timestamps are ISO-8601 UTC
strings exactly as stored (TEXT columns); the FE formats them. Requester /
resolver identity fields are present only for owner callers (maintainers see
the role alone). ``ConfigDict(extra="allow")`` per the schema convention.

FE-facing: re-exported from ``fe_types.py`` and code-generated into
``inspector/frontend/src/lib/types/generated/schemas.ts`` via
``scripts/codegen/regen_fe_types.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .align_runs import AlignQuota, AlignRunStatus


class RequestChange(BaseModel):
    """One proposed-edit field, with the current catalog value as ``from``.

    Mirrors a single ``ProposedEdits`` field. ``from``/``to`` carry strings or
    numbers (recording_year) or null.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    field: str
    label: str
    # `from` is a Python keyword — store as `from_`, emit as `from` for the FE.
    from_: str | int | None = Field(default=None, alias="from")
    to: str | int | None = None


class AdminRequestRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    slug: str | None = None
    # existing_combo_edit (slug-based) | existing_reciter_new_combo | new_reciter
    kind: str
    status: str  # pending | accepted | returned | discarded
    submitted_at: str
    resolved_at: str | None = None
    resolution_reason: str | None = None
    auto_claim: bool = False
    comments: str | None = None

    # catalog-joined display fields. For slugless intake rows these come from
    # the payload (new_reciter identity) or a reciter_id join (new_combo).
    reciter_id: str | None = None
    name_en: str | None = None
    name_ar: str | None = None
    riwayah: str | None = None
    style: str | None = None

    proposed_edits: dict = Field(default_factory=dict)
    changes: list[RequestChange] = Field(default_factory=list)
    conflict: bool = False

    # intake-only payload (audio source + last reachability probe). Absent
    # (None) for slug-based edit requests.
    source: dict | None = None
    probe: dict | None = None

    # Native align pipeline: the slug's active/latest run, for open slug rows.
    align: AlignRunStatus | None = None

    # per-caller overlay
    requester_role: str | None = None
    requester_login: str | None = None  # owner-only
    requester_hf_user_id: str | None = None  # owner-only
    resolved_by_role: str | None = None
    resolved_by_login: str | None = None  # owner-only


class AdminRequestCounts(BaseModel):
    model_config = ConfigDict(extra="allow")

    open: int = 0
    accepted: int = 0
    returned: int = 0
    discarded: int = 0


class AdminRequestsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    rows: list[AdminRequestRow] = Field(default_factory=list)
    counts: AdminRequestCounts = Field(default_factory=AdminRequestCounts)
    #: Shared align budget (open facet, ``intake.align`` holders only).
    align_quota: AlignQuota | None = None
