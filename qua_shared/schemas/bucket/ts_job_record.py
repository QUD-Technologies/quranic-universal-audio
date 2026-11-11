"""Timestamps run record — one per run, at ``reciters/<slug>/jobs/ts/<run_id>.json``.

``inspector/services/admin/ts_aligner_runner.py`` writes the ``running`` record
at launch and stamps it ``succeeded`` / ``failed`` / ``canceled`` with its logs
when the run ends; ``inspector/services/admin/timestamps_jobs.py`` reads it for
status and rewrites it on cancel or after an interrupted boot. The Reviews-tab
side panel reads these so a run's scope + logs stay viewable after it ends.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TsJobSettings(BaseModel):
    """A run's scope.

    ``extra="ignore"``: records written by the MFA engine also carry its
    alignment knobs (``beams``, ``aligner_model``, ``workers`` …); they parse
    and are dropped.
    """

    model_config = ConfigDict(extra="ignore")

    #: Regenerate only these chapters (surah numbers). ``None`` = full reciter.
    #: Untouched chapters keep their existing shards; the run merges (not
    #: clobbers) the whole-reciter ``ts_validation.json``.
    chapters: list[int] | None = None


class TsJobRecord(BaseModel):
    """One job run's durable record (settings + status + logs).

    ``status`` mirrors HF's lowercased stage (``running`` / ``succeeded`` /
    ``failed`` / ``error`` / ``timed-out`` …). ``logs`` is a bounded tail;
    ``log_truncated`` flags that earlier lines were dropped.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    slug: str
    type: str = "ts"  # job kind — matches the jobs/<type>/ bucket namespace
    settings: TsJobSettings = Field(default_factory=TsJobSettings)
    status: str = "running"
    #: Chapters this run actually re-aligned (echoes ``settings.chapters``);
    #: ``None`` = full reciter. Operational audit for partial regenerations.
    chapters_refreshed: list[int] | None = None
    started_at: str | None = None  # ISO-8601 UTC
    ended_at: str | None = None  # ISO-8601 UTC
    url: str | None = None  # HF job URL
    logs: list[str] = Field(default_factory=list)
    log_truncated: bool = False
    error: str | None = None
