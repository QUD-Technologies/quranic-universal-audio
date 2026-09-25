"""Unified job record — the single shape every job kind persists.

One store, one schema. The Inspector server writes a ``JobRecord`` to the
bucket for **every** kind (``timestamps`` / ``hf_publish`` /
``hf_publish_batch`` / ``cut_release`` / ``refresh_catalog``) at launch
(``status="running"``) and at every terminal point (``succeeded`` /
``failed``), via ``services/admin/jobs/records.py``. The DB stays the
source-of-truth for *releases*; this record is the uniform observability /
audit artifact that the admin **Jobs** tab reads from one place.

``JobRecord`` is a superset of the legacy ``TsJobRecord`` (which the TS HF-Job
container still self-writes, unchanged) — old TS records parse cleanly into it
(same field names + ``extra="allow"``). ``records.read`` injects ``kind`` from
the bucket path when an older record lacks it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ts_job_record import TsJobSettings

JobKind = Literal[
    "timestamps",
    "hf_publish",
    "hf_publish_batch",
    "cut_release",
    "refresh_catalog",
    "acquire_audio",
    "split_audio",
]

#: Terminal vs in-flight statuses, normalized to a small vocabulary the FE
#: buckets on. ``running`` covers HF ``running``/``pending``/``updating``.
JobStatus = Literal["running", "succeeded", "failed"]


class JobMember(BaseModel):
    """One recitation's outcome inside a batch publish or a GH cut.

    Kept slim — the heavy cut membership (catalog snapshot, zip bytes) stays in
    the DB; only what the Jobs drawer renders is recorded here.
    """

    model_config = ConfigDict(extra="allow")

    slug: str | None = None
    status: str | None = None
    version: str | None = None
    external_uri: str | None = None
    change_kind: str | None = None
    error: str | None = None


class JobRecord(BaseModel):
    """One job run's durable record — uniform across every kind.

    ``status`` is normalized to ``running`` / ``succeeded`` / ``failed`` at the
    write site. Kind-specific extras: ``settings`` + ``logs`` for timestamps;
    ``members`` for batch / cut; ``version`` + ``external_uri`` for the release
    tracks.
    """

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    job_id: str
    kind: JobKind = "timestamps"
    slug: str | None = None
    status: str = "running"
    started_at: str | None = None  # ISO-8601 UTC
    ended_at: str | None = None  # ISO-8601 UTC
    url: str | None = None  # HF job page URL
    launched_by: str | None = None  # hf_user_id / login that triggered it

    # Release-track fields (hf_publish / cut_release / batch members).
    version: str | None = None
    external_uri: str | None = None
    validation_summary: dict | None = None
    members: list[JobMember] = Field(default_factory=list)
    member_count: int | None = None

    # Timestamps-kind fields.
    settings: TsJobSettings | None = None
    chapters_refreshed: list[int] | None = None
    logs: list[str] = Field(default_factory=list)
    log_truncated: bool = False
    error: str | None = None


class JobsListResponse(BaseModel):
    """Payload for ``GET /api/admin/jobs`` — the unified list + a running tally."""

    model_config = ConfigDict(extra="allow")

    jobs: list[JobRecord] = Field(default_factory=list)
    running_count: int = 0
