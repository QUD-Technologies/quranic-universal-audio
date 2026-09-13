"""Mark-ready submission wire schemas.

A reviewer "marks ready" by submitting a checklist + two optional comment
boxes. The handler in ``services/state/state.py`` validates this payload,
then ``repo_claims.set_marked_ready`` persists it on the open claim. The
admin Reviews drawer surfaces both checklist + comments back to the
maintainer reviewing the submission.

The five checklist keys MUST stay in lockstep with the FE copy module at
``inspector/frontend/src/tabs/segments/copy/mark-ready/index.ts`` — both
sides declare them as a literal union and a parity test in the FE
``__tests__`` guards drift.

FE-facing: re-exported from ``fe_types.py`` and code-generated into
``inspector/frontend/src/lib/types/generated/schemas.ts`` via
``scripts/codegen/regen_fe_types.py``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The six checklist keys — string-literal union for type safety in both
# layers. New keys here MUST be mirrored in the FE copy module (single
# source of truth at the wire shape level).
ChecklistKey = Literal[
    "failed_alignments",
    "missing_words",
    "low_confidence",
    "repetitions",
    "splits_wasl_waqf",
    "basmala_amin_intros",
]

# The validation category_counts that gate submission. Both client and
# server independently enforce these are zero — the server is
# authoritative against the live segs on disk. ``repetitions`` is an
# ignorable category like ``low_confidence``: any detected rep must be
# split or marked ignored before the reviewer can mark ready.
#
# An owner-only category (registry ``owner_only``) must never appear here:
# it is invisible to the reviewer, so blocking on it would leave them
# staring at a count they cannot open, let alone resolve. That is why
# ``boundary_adj`` is absent despite still being classified and surfaced
# to owners. ``mark-ready-copy.test.ts`` asserts the rule.
BLOCKING_COUNT_KEYS: tuple[str, ...] = (
    "low_confidence",
    "low_confidence_v2",
    "cross_verse",
    "basmala_amin",
    "repetitions",
)


class MarkReadyChecklist(BaseModel):
    """The six attestation checkboxes. All MUST be True at submit time —
    the handler raises ``InvalidTransition`` otherwise.

    Keys are repeated in the FE copy module as a literal union; if you
    add one here, update the copy module, the markdown file, and the
    parity test.

    ``repetitions`` is a separate attestation from ``low_confidence``
    because repetitions, while also an ignorable category, exercises a
    distinct reviewer pass (detected-rep autosplits + listen-through).
    """

    model_config = ConfigDict(extra="forbid")

    failed_alignments: bool
    missing_words: bool
    low_confidence: bool
    repetitions: bool
    splits_wasl_waqf: bool
    basmala_amin_intros: bool


class MarkReadyRequest(BaseModel):
    """Request body for ``POST /api/mark-ready/<slug>``.

    Comment fields are optional and default to empty strings (not None) so
    the persisted-vs-omitted distinction is byte-stable through the wire.
    """

    model_config = ConfigDict(extra="forbid")

    checklist: MarkReadyChecklist
    comment_checks: str = Field(default="", max_length=4000)
    comment_issues: str = Field(default="", max_length=4000)


class MarkReadySubmission(BaseModel):
    """Persisted shape of a mark-ready submission, read back by the admin
    Reviews drawer. Cleared together with ``marked_ready_at`` on
    unmark / release / reassign so a sent-back row presents as fresh.

    ``checklist`` is ``None`` for owner-bypass submissions (the holder of
    ``claim.mark_ready_skip_gates`` skipped the form entirely); the admin
    drawer renders a "submitted via owner bypass" pill in that case.
    ``bypass_used`` is the authoritative flag for that branch — the
    handler stamps it server-side, never trusting the FE to set it.
    """

    model_config = ConfigDict(extra="allow")

    checklist: MarkReadyChecklist | None = None
    comment_checks: str = ""
    comment_issues: str = ""
    bypass_used: bool = False
