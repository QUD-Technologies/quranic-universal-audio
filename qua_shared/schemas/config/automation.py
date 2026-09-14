"""Owner-configurable release automations — persisted config blob.

One ``AutomationConfig`` (a single JSON blob in ``automation_config``) drives the
release-automation reconciler (``inspector/services/admin/automation/``): a 60 s
daemon that watches live release state and fires the existing jobs on the
owner's behalf. Six independent automations, each enable/disable:

- ``auto_gen_ts``          — generate timestamps for a marked-ready recitation
                             that passes the comment/flag gates.
- ``gh_cut``               — cut a global GH release on a schedule (owner tz).
- ``hf_publish``           — batch-publish fresh + stale HF candidates on a
                             schedule.
- ``stale_ts_regen``       — regenerate timestamps once segment edits settle.
- ``stale_metadata``       — refresh the HF catalog once a metadata edit settles.
- ``auto_release_inactive``— release a reviewer's claim once they've been
                             inactive for N days (frees the recitation to be
                             re-claimed; notifies the reviewer).

It also carries ``ts_generation_defaults`` (``TsGenerationDefaults``) — the single
owner-wide source for the timestamps-generation knobs (beam/model/workers/
batch_size/download_workers/padding/method) shared by the manual launch form, the
auto-gen / stale-regen automations, and the HF job. The Releases-tab
"Timestamps generation" accordion edits this blob.

This is the owner *config*; per-automation runtime state (last run / result)
lives in the ``automation_state`` table, not here. The FE consumes the
codegen'd types, so it is re-exported via ``fe_types.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Default scheduling — every week at 09:00 in the owner's timezone.
_DEFAULT_INTERVAL_DAYS = 7
_DEFAULT_TIME_OF_DAY = "09:00"
_DEFAULT_TIMEZONE = "Australia/Sydney"
#: Default debounce before acting on a settled edit (one hour).
_DEFAULT_GUARD_MINUTES = 60
#: Default reviewer-inactivity window before a claim is auto-released.
_DEFAULT_INACTIVE_DAYS = 14
#: Alignment beam shared with the manual TS launch form.
_DEFAULT_BEAM = 50

_TIME_OF_DAY_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class TsGenerationDefaults(BaseModel):
    """Owner-wide default knobs for every timestamps-generation surface.

    The single shared source the Releases-tab "Timestamps generation" accordion
    edits. It is the sole source of the TS tunables for every TS launch — the
    manual form (``routes/admin/reviews.py::_parse_ts_settings``), the auto-gen /
    stale-regen automations (``services/admin/automation/evaluators.py``) and,
    through them, the HF job (``qua_jobs/generate_timestamps.py``). Every TS
    tunable comes from here; the only per-launch input is the manual form's
    ``chapters`` scope. A field unset here cedes to the job's ``DEFAULT_*``.

    ``beam`` is the single alignment beam. Probe beams are gone: a run aligns
    once, under this beam, and nothing is re-aligned to cross-check it.
    ``padding`` / ``method`` are the pipeline tunables (``None`` → the job's
    ``DEFAULT_PADDING`` / ``DEFAULT_METHOD``).
    """

    beam: int = Field(default=_DEFAULT_BEAM, ge=1)
    #: Acoustic model (catalog id). ``None`` = the store's default model.
    aligner_model: str | None = None
    #: Aligner worker count. ``None`` = the job's capped default.
    workers: int | None = Field(default=None, ge=1, le=64)
    #: Batch size for alignment. ``None`` = the job's ``DEFAULT_BATCH_SIZE``.
    batch_size: int | None = Field(default=None, ge=1)
    #: Concurrent audio downloads. ``None`` = the job's ``DEFAULT_DOWNLOAD_WORKERS``.
    download_workers: int | None = Field(default=None, ge=1)
    #: Phoneme gap-padding strategy (``forward`` | ``symmetric`` | ``none``).
    padding: str | None = None
    #: Alignment method (``kalpy`` | …). ``None`` = the job's ``DEFAULT_METHOD``.
    method: str | None = None


class AutoGenTsConfig(BaseModel):
    """Auto-generate timestamps when a recitation is marked ready.

    Acts on the ``ready_to_generate`` bucket (marked-ready, no TS yet). The two
    gates mirror the reviewer's mark-ready submission: skip when they left a
    written comment (``gate_by_comments``) or flagged any segment
    (``gate_by_flags``). A checklist-bypass submission is always skipped.

    The TS tunables (beam/model/…) come from the shared
    ``ts_generation_defaults``; ``extra="allow"`` tolerates older saved blobs that
    still carry the retired per-automation ``beam`` / ``aligner_model`` keys.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    gate_by_comments: bool = True
    gate_by_flags: bool = True


class GhCutConfig(BaseModel):
    """Cut a global GH release on a fixed cadence (owner timezone).

    Fires every ``interval_days`` at ``time_of_day`` in ``timezone``, but only
    when the preview shows real change (added or refreshed recitations).
    ``next_version_override`` is a one-shot: applied to the next auto cut, then
    cleared on success so the cadence reverts to auto-computed versions.
    """

    enabled: bool = False
    interval_days: int = Field(default=_DEFAULT_INTERVAL_DAYS, ge=1)
    time_of_day: str = Field(default=_DEFAULT_TIME_OF_DAY, pattern=_TIME_OF_DAY_RE)
    timezone: str = Field(default=_DEFAULT_TIMEZONE, min_length=1)
    next_version_override: str | None = None


class HfPublishConfig(BaseModel):
    """Batch-publish the HF dataset on a fixed cadence (owner timezone).

    Fires every ``interval_days`` at ``time_of_day`` in ``timezone``, collecting
    every fresh (``publish_hf``) and stale (``republish_hf``) candidate into ONE
    batch job. Skips the window when there are no candidates.
    """

    enabled: bool = False
    interval_days: int = Field(default=_DEFAULT_INTERVAL_DAYS, ge=1)
    time_of_day: str = Field(default=_DEFAULT_TIME_OF_DAY, pattern=_TIME_OF_DAY_RE)
    timezone: str = Field(default=_DEFAULT_TIMEZONE, min_length=1)


class StaleTsRegenConfig(BaseModel):
    """Regenerate timestamps once segment edits have settled.

    Acts on the ``behind_edits`` bucket. ``guard_minutes`` is the debounce: fire
    only when the latest timestamp-affecting edit is older than the guard, so
    consecutive edits coalesce into one regen. ``scope`` picks full-reciter vs
    just the affected chapters.

    The TS tunables (beam/…) come from the shared
    ``ts_generation_defaults``; ``extra="allow"`` tolerates older saved blobs that
    still carry the retired per-automation ``beam`` / ``probe_beams`` keys.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    guard_minutes: int = Field(default=_DEFAULT_GUARD_MINUTES, ge=0)
    scope: Literal["full", "affected"] = "affected"


class StaleMetadataConfig(BaseModel):
    """Refresh the HF catalog once a catalog-metadata edit has settled.

    Acts on HF rows stale for ``catalog_edit``. ``guard_minutes`` debounces the
    same way as ``stale_ts_regen``; the cheap ``refresh_catalog`` job clears the
    stamp without a full republish.
    """

    enabled: bool = False
    guard_minutes: int = Field(default=_DEFAULT_GUARD_MINUTES, ge=0)


class AutoReleaseInactiveConfig(BaseModel):
    """Release a reviewer's claim after a period of inactivity.

    Acts on open claims that are under review and NOT yet marked ready (a
    marked-ready claim is complete and awaiting the pipeline, not idle). A claim
    is "inactive" when the reviewer's last activity — the later of the claim time
    and their most recent segment edit — is older than ``inactive_days``. Release
    routes through the same ``claim.force_released`` transition the manual path
    uses, so the reviewer gets the identical "your review was released" notice and
    the recitation returns to the awaiting-review pool.
    """

    enabled: bool = False
    inactive_days: int = Field(default=_DEFAULT_INACTIVE_DAYS, ge=1)


class AutomationConfig(BaseModel):
    """The owner's full automation configuration (one persisted blob).

    ``extra="allow"`` keeps an unknown automation key written by a newer FE from
    being dropped when an older server re-serializes the blob (forward-compat).
    """

    model_config = ConfigDict(extra="allow")

    #: Owner-wide TS-generation knobs shared by every gen surface (manual launch,
    #: auto-gen, stale-regen, and the HF job through them). Per-automation /
    #: per-form overrides still win; unset fields fall back here.
    ts_generation_defaults: TsGenerationDefaults = Field(default_factory=TsGenerationDefaults)
    auto_gen_ts: AutoGenTsConfig = Field(default_factory=AutoGenTsConfig)
    gh_cut: GhCutConfig = Field(default_factory=GhCutConfig)
    hf_publish: HfPublishConfig = Field(default_factory=HfPublishConfig)
    stale_ts_regen: StaleTsRegenConfig = Field(default_factory=StaleTsRegenConfig)
    stale_metadata: StaleMetadataConfig = Field(default_factory=StaleMetadataConfig)
    auto_release_inactive: AutoReleaseInactiveConfig = Field(
        default_factory=AutoReleaseInactiveConfig
    )

    @field_validator("gh_cut")
    @classmethod
    def _normalize_override(cls, v: GhCutConfig) -> GhCutConfig:
        """Blank/whitespace override → None (treat empty input as 'auto')."""
        if v.next_version_override is not None and not v.next_version_override.strip():
            return v.model_copy(update={"next_version_override": None})
        return v


class AutomationStateRow(BaseModel):
    """Per-automation runtime state for the FE status line (read-only to it).

    ``last_status`` is ``launched`` / ``skipped`` / ``error``; ``last_detail`` is
    a short human note (e.g. ``"launched ts job for foo-bar"`` or
    ``"no candidates"``). ``next_run_at`` is the engine's computed next fire for
    the scheduled automations (ISO-8601 UTC), null for the event-driven ones.
    """

    automation_id: str
    last_run_at: str | None = None
    last_status: str | None = None
    last_detail: str | None = None
    next_run_at: str | None = None


class AutomationResponse(BaseModel):
    """``GET /api/admin/releases/automation`` payload: config + state + preview."""

    config: AutomationConfig
    state: list[AutomationStateRow] = Field(default_factory=list)
    #: Auto-computed next GH tag for display (null when nothing is cuttable).
    next_version: str | None = None
