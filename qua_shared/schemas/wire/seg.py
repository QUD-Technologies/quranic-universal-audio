"""Segments-tab HTTP wire schemas — every ``/api/seg/*`` request + response.

Models the shapes the segments routes ACTUALLY emit (the producer is the
source of truth). Pinned to the live wire shape and verified against the
real route response in ``inspector/tests/routes/test_wire_seg_models.py``.

Routes covered (blueprint → file):

* ``seg_data``  (``routes/segments/data.py``)       — ``/config`` ``/reciters``
  ``/data/<reciter>/<chapter>`` ``/all/<reciter>``
* ``seg_edit``  (``routes/segments/edit.py``)       — ``/save`` ``/undo-batch``
  ``/undo-ops``
* ``seg_val``   (``routes/segments/validation.py``) — ``/validate/<reciter>``
* ``peaks``     (``routes/segments/peaks.py``)       — ``/peaks/<reciter>``
  ``/segment-peaks/<reciter>``

Conventions:

* Responses (we control the producer) → ``extra="forbid"``.
* Request bodies → ``extra="forbid"`` too; the route ignores unknown keys but
  the FE payload builders are the only sender and emit a closed key set.
* ``Literal`` for finite string fields the FE switches on (``state`` /
  ``visibility`` / ``q``).
* ``dict[str, T]`` for dynamic-keyed maps (TS ``Record<...>``).
* Optional fields default to ``None`` / empty so ``model_dump(exclude_none=True,
  by_alias=True)`` reproduces the on-wire key set exactly.

FE-facing: the subset the FE consumes is re-exported from ``fe_types.py`` and
codegen'd into ``inspector/frontend/src/lib/types/generated/schemas.ts``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

# ===========================================================================
# Shared scalar aliases (documentation-only; TS sees ``string`` / ``number``)
# ===========================================================================

# "surah:ayah:word" or word-range "S:A:W-S:A:W".
Ref = str
# "surah:ayah" (single) or "S:A-S:A2" (compound cross-verse).
VerseRef = str

# Lifecycle state + visibility enums the ``/reciters`` route emits as the
# ``.value`` of the SQLite-backed ReciterState / Visibility enums. Mirrored
# here as ``Literal`` so the FE gets a closed switch surface. The Segments tab
# lists WIP reciters too, so this MUST carry the FULL ``ReciterState`` set
# (``catalogued`` / ``awaiting_alignment`` included) — a narrower mirror 500s
# the reciter list for any pre-review reciter. Kept in lockstep with the
# canonical enums by ``tests/persistence/test_seg_state_parity.py``.
SegReciterState = Literal[
    "catalogued",
    "awaiting_alignment",
    "awaiting_review",
    "under_review",
    "released",
]
SegReciterVisibility = Literal["public", "discarded"]


# ===========================================================================
# /api/seg/config — display configuration
# ===========================================================================


class SegConfigResponse(BaseModel):
    """``GET /api/seg/config`` — display constants + validation vocab.

    ``seg_font_size`` / ``seg_word_spacing`` are CSS dimension STRINGS
    (``"1.8rem"`` / ``"0.2em"``), not numbers. ``accordion_context`` maps a
    validation category to a default reveal state (``"shown"`` / ``"hidden"``).

    The three coordinate vocabularies are EDITION-SPECIFIC — the route takes
    ``?riwayah=`` and echoes which edition it answered for, so the FE cannot
    render one edition's tables against another's script.
    """

    model_config = ConfigDict(extra="forbid")

    seg_font_size: str
    seg_word_spacing: str
    seg_scroll_anim_mode: str
    trim_pad_left: int
    trim_pad_right: int
    trim_dim_alpha: float
    low_conf_default_threshold: int
    validation_categories: list[str]
    #: Inspector slug of the edition the vocabularies below describe.
    riwayah: str
    #: Verses that OPEN with the disconnected letters. Verse-keyed because the
    #: boundary-adjustment exemption covers the whole verse, not just the
    #: opening word (13:1 runs on past the letters).
    muqattaat_verses: list[tuple[int, int]]
    qalqala_letters: list[str]
    standalone_refs: list[tuple[int, int, int]]
    standalone_words: list[str]
    #: Word refs that ARE a disconnected-letters opening. Distinct from
    #: ``muqattaat_verses``: a Warsh verse can hold two of them.
    muqattaat_words: list[tuple[int, int, int]]
    #: Confidence below which a segment is flagged; per edition (D19).
    low_confidence_threshold: float
    accordion_context: dict[str, str]


# ===========================================================================
# /api/seg/reciters — lifecycle-stamped reciter list
# ===========================================================================


class SegReciter(BaseModel):
    """One row of ``GET /api/seg/reciters``.

    ``audio_source`` is the delivery channel (``mp3quran`` / ``qul`` / ...),
    NOT a by_surah/by_ayah signal. ``state`` + ``visibility`` are the live
    lifecycle fields the route emits from the SQLite state row.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    audio_source: str
    audio_category: Literal["by_surah", "by_ayah"]
    state: SegReciterState
    visibility: SegReciterVisibility


class SegRecitersResponse(RootModel[list[SegReciter]]):
    """``GET /api/seg/reciters`` — a bare JSON array of reciter rows."""


# ===========================================================================
# Segment rows + flag view (shared by /data and /all)
# ===========================================================================


class FlagAuthor(BaseModel):
    """Author of a flag comment. ``role`` is always present; ``login`` / ``id``
    only when the viewer holds ``segments.see_flagger_identity`` (otherwise
    identity is redacted to the role)."""

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    login: str | None = None
    id: str | None = None


class FlagComment(BaseModel):
    """One comment in a flag thread (root or follow-up), redacted FE shape."""

    model_config = ConfigDict(extra="forbid")

    comment: str
    at: str | None = None
    author: FlagAuthor
    mine: bool


class SegmentFlagView(FlagComment):
    """A segment's flag thread: a root comment plus follow-up replies.

    Redacted + ``mine``-stamped server-side by ``services/segments/flags.py``;
    never the canonical persisted shape.
    """

    follow_ups: list[FlagComment] = Field(default_factory=list)


class SegWordTiming(BaseModel):
    """A word interval exposed for sample-review highlighting."""

    model_config = ConfigDict(extra="forbid")

    word: str
    location: Ref
    start_ms: int
    end_ms: int


class SegDataSegment(BaseModel):
    """A segment row as emitted by ``GET /api/seg/data`` (chapter-scoped).

    The route injects ``index`` (chapter-local), ``entry_idx``, and a flat
    ``audio_url`` onto each seg. ``ignored_categories`` / ``is_wasl`` are
    emitted only when truthy on the underlying seg (so they are optional on
    the wire — ``exclude_none`` / default-drop reproduces the key set).
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    entry_idx: int
    time_start: int
    time_end: int
    matched_ref: Ref
    confidence: float
    audio_url: str
    ignored_categories: list[str] | None = None
    is_wasl: bool | None = None
    word_timings: list[SegWordTiming] | None = None


class SegAllSegment(BaseModel):
    """A segment row as emitted by ``GET /api/seg/all`` (all-chapter scope).

    Differs from the ``/data`` row: carries ``chapter`` + ``segment_uid`` +
    ``entry_ref`` and OMITS ``audio_url`` (redundant with the top-level
    ``audio_by_chapter`` map). ``wrap_word_ranges`` / ``ignored_categories`` /
    ``is_wasl`` / ``flag`` are emitted only when present on the seg.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int
    entry_idx: int
    index: int
    segment_uid: str
    time_start: int
    time_end: int
    matched_ref: Ref
    confidence: float
    entry_ref: str
    wrap_word_ranges: list | None = None
    ignored_categories: list[str] | None = None
    is_wasl: bool | None = None
    flag: SegmentFlagView | None = None
    word_timings: list[SegWordTiming] | None = None


class SegmentsChapterSummary(BaseModel):
    """Per-chapter summary stats block inside ``GET /api/seg/data``."""

    model_config = ConfigDict(extra="forbid")

    total_segments: int
    matched_segments: int
    failed_segments: int
    conf_min: float
    conf_median: float
    conf_mean: float
    conf_max: float
    below_60: int
    below_80: int
    total_speech_ms: int
    avg_segment_ms: int
    total_silence_ms: int
    avg_silence_ms: int
    issue_indices: list[int]
    missing_verses: list[VerseRef]


class SegDataResponse(BaseModel):
    """``GET /api/seg/data/<reciter>/<chapter>[?verse=:n]`` success body.

    ``chapter_bitrate_kbps`` is a sparse ``{chapter -> kbps}`` map (JSON object
    keys are strings on the wire). ``error`` is emitted on the 404 variant
    (reciter/chapter not found) — the success and error bodies are
    structurally distinct, so ``error`` stays optional here.
    """

    model_config = ConfigDict(extra="forbid")

    audio_url: str
    vbr: bool
    reciter_vbr_chapters: list[int]
    chapter_bitrate_kbps: dict[str, int] = Field(default_factory=dict)
    segments: list[SegDataSegment]
    summary: SegmentsChapterSummary
    error: str | None = None


class SegAllResponse(BaseModel):
    """``GET /api/seg/all/<reciter>`` success body.

    ``audio_by_chapter`` / ``chapter_duration_ms_by_chapter`` are chapter-keyed
    string maps; ``duration_ms_by_url`` is URL-keyed. ``pad_ms`` is the legacy
    symmetric shim ``(pad_left_ms + pad_right_ms) // 2``.
    """

    model_config = ConfigDict(extra="forbid")

    #: Inspector riwayah slug of THIS delivery — authoritative, cross-checked
    #: against ``detailed.json`` server-side. The tab speculatively loads the
    #: reference bundle from the catalog snapshot (fast, and right whenever the
    #: catalog is loaded); this field is what it reconciles against, so a
    #: delivery can never be rendered under another edition's coordinates
    #: because the catalog happened to arrive late.
    riwayah: str
    segments: list[SegAllSegment]
    audio_by_chapter: dict[str, str] = Field(default_factory=dict)
    chapter_duration_ms_by_chapter: dict[str, int] = Field(default_factory=dict)
    duration_ms_by_url: dict[str, int] = Field(default_factory=dict)
    reciter_vbr_chapters: list[int] = Field(default_factory=list)
    pad_ms: int
    pad_left_ms: int
    pad_right_ms: int
    min_silence_floor_ms: int


# ===========================================================================
# /api/seg/save + /undo-batch + /undo-ops — edit request + response bodies
# ===========================================================================


class SegSavePatchSegment(BaseModel):
    """One segment in a ``patch`` (field-level) save payload.

    The patch path keys updates by chapter-local ``index`` and only rewrites
    ``matched_ref`` / ``confidence`` / ``ignored_categories``.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    segment_uid: str
    matched_ref: Ref
    confidence: float
    ignored_categories: list[str] | None = None
    is_wasl: bool | None = None


class SegSaveFullSegment(BaseModel):
    """One segment in a ``full_replace`` (structural) save payload."""

    model_config = ConfigDict(extra="forbid")

    time_start: int
    time_end: int
    matched_ref: Ref
    confidence: float
    audio_url: str
    wrap_word_ranges: list | None = None
    ignored_categories: list[str] | None = None
    is_wasl: bool | None = None


class SegSaveRequest(BaseModel):
    """``POST /api/seg/save/<reciter>/<chapter>`` request body.

    ``full_replace`` flags a structural save (segments are
    ``SegSaveFullSegment``); its absence is a field-level patch save (segments
    are ``SegSavePatchSegment``). The route reads ``operations`` (NOT ``ops``);
    each op is the loose FE edit-op envelope persisted verbatim into
    ``edit_history.jsonl`` (modelled permissively as dicts here — the canonical
    op vocabulary lives in ``EditOperation`` in ``bucket/edit_history.py``).
    """

    model_config = ConfigDict(extra="forbid")

    segments: list[dict]
    operations: list[dict] = Field(default_factory=list)
    full_replace: bool | None = None


class SegSaveResponse(BaseModel):
    """``POST /api/seg/save`` success body — the route returns ``{"ok": true}``."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True


class SegUndoBatchRequest(BaseModel):
    """``POST /api/seg/undo-batch/<reciter>`` request body."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str


class SegUndoOpsRequest(BaseModel):
    """``POST /api/seg/undo-ops/<reciter>`` request body."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    op_ids: list[str]


class SegUndoResponse(BaseModel):
    """Success body for both undo routes — ``{"ok": true, "operations_reversed": N}``."""

    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    operations_reversed: int


# ===========================================================================
# /api/seg/validate — per-category item variants + response
# ===========================================================================
#
# NOTE ON THE "DISCRIMINATED UNION": the validation items carry NO intrinsic
# on-wire discriminator field. The category is the RESPONSE KEY (``failed`` /
# ``low_confidence`` / ...), and the FE switches on that key (passed as a
# ``category`` prop to GenericIssueCard, never read off the item). A faithful
# model therefore CANNOT use a Pydantic ``Field(discriminator=...)`` union —
# that would require inventing an off-wire ``kind`` field and violate
# ``extra="forbid"`` + "model what the route emits". ``SegValAnyItem`` is the
# plain ``Union`` over every variant (RootModel-wrapped for codegen): json2ts
# renders it as a clean named union ``SegValFailedItem | SegValMissingVerseItem
# | ...`` — NOT ``unknown`` soup — because every member is a ``$ref`` under a
# top-level ``anyOf``.


class SegValAutoFix(BaseModel):
    """Auto-fix descriptor attached to some ``missing_words`` entries."""

    model_config = ConfigDict(extra="forbid")

    target_seg_index: int
    new_ref_start: str
    new_ref_end: str


class SegValFailedItem(BaseModel):
    """``failed`` — a segment that did not align to any verse."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    time: str
    classified_issues: list[str] = Field(default_factory=list)


class SegValMissingVerseItem(BaseModel):
    """``missing_verses`` — a verse with zero covering segments (chapter-level)."""

    model_config = ConfigDict(extra="forbid")

    verse_key: VerseRef
    chapter: int
    segment_uid: str | None = None
    msg: str


class SegValMissingWordsItem(BaseModel):
    """``missing_words`` — a verse missing some words (chapter-level).

    ``segment_uid`` is always ``null`` on these (verse-scoped, not per-segment).
    ``auto_fix`` / ``auto_fix_up`` / ``auto_fix_down`` and ``sequence_gap``
    appear conditionally.
    """

    model_config = ConfigDict(extra="forbid")

    verse_key: VerseRef
    chapter: int
    segment_uid: str | None = None
    msg: str
    missing_words: list[int] = Field(default_factory=list)
    seg_indices: list[int] = Field(default_factory=list)
    sequence_gap: bool | None = None
    auto_fix: SegValAutoFix | None = None
    auto_fix_up: SegValAutoFix | None = None
    auto_fix_down: SegValAutoFix | None = None


class SegValStructuralErrorItem(BaseModel):
    """``errors`` / ``structural_errors`` — a structural integrity violation."""

    model_config = ConfigDict(extra="forbid")

    verse_key: VerseRef
    chapter: int
    segment_uid: str | None = None
    msg: str


class SegValLowConfidenceItem(BaseModel):
    """``low_confidence`` — a segment below the confidence threshold."""

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    confidence: float
    classified_issues: list[str] = Field(default_factory=list)


class SegValLowConfidenceV2Item(BaseModel):
    """``low_confidence_v2`` — a segment flagged by the extraction-time MFA
    tight-beam probe. No confidence score; the signal is binary."""

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    classified_issues: list[str] = Field(default_factory=list)


class SegValHiddenPauseCut(BaseModel):
    """One proposed cut inside a segment (``hidden_pause_v1`` /
    ``missed_waqf_v1`` sidecars).

    ``axes`` names the offline arms that agree on the cut (``trio`` = collar
    boundary head, ``lite`` = lite student, ...). ``evidence`` is the
    per-axis raw measurement block, passed through for the card."""

    model_config = ConfigDict(extra="forbid")

    cursor_ms: int | None = None
    axes: list[str] = Field(default_factory=list)
    gap_ms: int | None = None
    score: int | None = None
    word: str | None = None
    final_class: str | None = None
    verse_end: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class SegValHiddenPauseBoundary(BaseModel):
    """Sidecar payload on a ``hidden_pause`` / ``missed_waqf`` item. ``refs`` is ``null`` when
    the offline pass could not assign per-section refs (Auto Split falls back
    to plain Split). ``score`` = agreeing axes × 1000 + min(gap_ms, 999)."""

    model_config = ConfigDict(extra="forbid")

    cursors: list[int] = Field(default_factory=list)
    refs: list[str] | None = None
    score: int = 0
    cuts: list[SegValHiddenPauseCut] = Field(default_factory=list)


class SegValHiddenPauseItem(BaseModel):
    """``hidden_pause`` — offline re-segmentation heard a pause inside this
    segment. Review-only (owner / maintainer capability)."""

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    classified_issues: list[str] = Field(default_factory=list)
    boundary: SegValHiddenPauseBoundary


class SegValMissedWaqfItem(BaseModel):
    """``missed_waqf`` — an offline phoneme + silence detector heard the
    reciter stop inside this segment, which was not cut there. Review-only
    (owner / maintainer capability).

    ``resolved`` marks an item already labelled — split from the card (edit
    history carries the split) or ignored (every cut WASL). The accordion keeps
    it so the labels stay reviewable; resolved items are excluded from
    ``category_counts``. ``segment_uid`` is the split ROOT uid.
    """

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    classified_issues: list[str] = Field(default_factory=list)
    boundary: SegValHiddenPauseBoundary
    resolved: bool = False


class SegValFalseSplitBoundary(BaseModel):
    """Sidecar payload on a ``false_split`` / ``unmarked_wasl`` item: the
    join under review is to ``next_uid`` (the following segment)."""

    model_config = ConfigDict(extra="forbid")

    next_uid: str | None = None
    axes: list[str] = Field(default_factory=list)
    gap_ms: int | None = None
    score: int = 0
    word: str | None = None
    final_class: str | None = None
    verse_end: bool = False
    is_wasl: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class SegValFalseSplitItem(BaseModel):
    """``false_split`` — offline re-segmentation heard continuous speech
    across this segment's end. Review-only (owner / maintainer capability)."""

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    classified_issues: list[str] = Field(default_factory=list)
    boundary: SegValFalseSplitBoundary


class SegValUnmarkedWaslItem(BaseModel):
    """``unmarked_wasl`` — every offline re-segmentation arm read straight
    through this segment's verse-to-verse join, yet the delivery never marked
    it ``is_wasl``. Review-only (owner / maintainer capability)."""

    model_config = ConfigDict(extra="forbid")

    ref: Ref
    chapter: int
    seg_index: int
    segment_uid: str | None = None
    classified_issues: list[str] = Field(default_factory=list)
    boundary: SegValFalseSplitBoundary


class SegValBoundaryAdjItem(BaseModel):
    """``boundary_adj`` — a segment whose boundary may need adjustment."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    verse_key: VerseRef
    classified_issues: list[str] = Field(default_factory=list)


class SegValCrossVerseItem(BaseModel):
    """``cross_verse`` — a segment spanning a verse boundary.

    ``resolved`` marks an item that no longer spans a boundary because it was
    split from the cross-verse card (edit history carries the split); the
    accordion keeps it so the WASL/WAQF picks stay reviewable. Resolved items
    are excluded from ``category_counts`` and never block mark-ready.
    ``segment_uid`` is the split ROOT uid (the live first piece).
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    classified_issues: list[str] = Field(default_factory=list)
    resolved: bool = False


class SegValAudioBleedingItem(BaseModel):
    """``audio_bleeding`` — a by_ayah segment matching a verse outside its
    own audio file's entry."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    entry_ref: str
    matched_verse: str
    ref: Ref
    confidence: float
    time: str
    msg: str
    classified_issues: list[str] = Field(default_factory=list)


class SegValRepetitionItem(BaseModel):
    """``repetitions`` — a segment carrying repeated (loopback) word ranges."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    display_ref: Ref
    confidence: float
    time: str
    text: str
    classified_issues: list[str] = Field(default_factory=list)


class SegValMuqattaatItem(BaseModel):
    """``muqattaat`` — a segment opening on huruf muqatta'at (display-only)."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    classified_issues: list[str] = Field(default_factory=list)


class SegValQalqalaItem(BaseModel):
    """``qalqala`` — a segment ending on a qalqala letter (display-only)."""

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    qalqala_letter: str
    end_of_verse: bool
    classified_issues: list[str] = Field(default_factory=list)


class SegValBasmalaAminItem(BaseModel):
    """``basmala_amin`` — a basmala/amin verse-boundary convention candidate.

    ``missed_basmala`` is stamped ``true`` on the augmented "possibly missed
    Basmala" candidates (first seg of a chapter whose Basmala was not stripped);
    absent on the canonical 1:1 / 1:7 candidates.
    """

    model_config = ConfigDict(extra="forbid")

    chapter: int
    seg_index: int
    segment_uid: str | None = None
    ref: Ref
    # ``time`` rides through only on the augmented "missed Basmala" candidates
    # (``{**first_seg, ...}`` in detail.py — first_seg carries it); the
    # canonical 1:1 / 1:7 candidates omit it. Optional, not required.
    time: str | None = None
    missed_basmala: bool | None = None
    classified_issues: list[str] = Field(default_factory=list)


# Plain union over every variant — NOT a Pydantic discriminator union (no
# shared on-wire discriminator field exists). json2ts renders this as a named
# TS union of the member interfaces.
SegValAnyItemUnion = (
    SegValFailedItem
    | SegValMissingVerseItem
    | SegValMissingWordsItem
    | SegValStructuralErrorItem
    | SegValLowConfidenceItem
    | SegValLowConfidenceV2Item
    | SegValHiddenPauseItem
    | SegValMissedWaqfItem
    | SegValFalseSplitItem
    | SegValUnmarkedWaslItem
    | SegValBoundaryAdjItem
    | SegValCrossVerseItem
    | SegValAudioBleedingItem
    | SegValRepetitionItem
    | SegValMuqattaatItem
    | SegValQalqalaItem
    | SegValBasmalaAminItem
)


class SegValAnyItem(RootModel[SegValAnyItemUnion]):
    """Any one validation item, as a named union of every per-category variant.

    The category is determined by the enclosing response key, not by a field
    on the item — there is no on-wire discriminator (see the module note).
    """


class SegValProbeMeta(BaseModel):
    """``low_confidence_v2_meta`` — provenance of the MFA tight-beam probe
    sidecar. Open shape: the ``_meta`` block of ``low_confidence_v2.json`` is
    passed through verbatim from the extraction stage."""

    model_config = ConfigDict(extra="allow")


class SegValBoundaryMeta(BaseModel):
    """``hidden_pause_meta`` / ``missed_waqf_meta`` / ``false_split_meta`` /
    ``unmarked_wasl_meta`` —
    provenance of the offline boundary-review sidecars (arms, segment counts,
    by-axes tallies). Open shape: the ``_meta`` block is passed through
    verbatim."""

    model_config = ConfigDict(extra="allow")


class SegValStats(BaseModel):
    """``stats`` — per-reciter structural segmentation statistics."""

    model_config = ConfigDict(extra="forbid")

    segments: int
    single: int
    multi_verses: int
    multi_segs: int
    cross_verse: int
    max_segs: int
    seg_dur_min: float
    seg_dur_med: float
    seg_dur_mean: float
    seg_dur_max: float
    pause_dur_min: float
    pause_dur_med: float
    pause_dur_mean: float
    pause_dur_max: float


class SegValidateResponse(BaseModel):
    """``GET /api/seg/validate/<reciter>`` — issues grouped by category.

    ``errors`` and ``structural_errors`` are the SAME list under two keys
    (additive alias). ``category_counts`` mirrors the per-category lengths in
    registry-declared order. ``split_group_index`` maps a root segment uid to
    its transitive split-descendant uids. ``low_confidence_v2_meta`` /
    ``hidden_pause_meta`` / ``missed_waqf_meta`` / ``false_split_meta`` /
    ``unmarked_wasl_meta`` are present only when the sidecar carried a
    ``_meta`` block. ``hidden_pause`` / ``missed_waqf`` / ``false_split`` /
    ``unmarked_wasl`` (and their metas) are omitted for
    viewers without ``segments.view_boundary_review``. Each item carries a
    ``classified_issues`` field.
    """

    model_config = ConfigDict(extra="forbid")

    errors: list[SegValStructuralErrorItem] = Field(default_factory=list)
    structural_errors: list[SegValStructuralErrorItem] = Field(default_factory=list)
    missing_verses: list[SegValMissingVerseItem] = Field(default_factory=list)
    missing_words: list[SegValMissingWordsItem] = Field(default_factory=list)
    failed: list[SegValFailedItem] = Field(default_factory=list)
    low_confidence: list[SegValLowConfidenceItem] = Field(default_factory=list)
    low_confidence_v2: list[SegValLowConfidenceV2Item] = Field(default_factory=list)
    hidden_pause: list[SegValHiddenPauseItem] | None = None
    missed_waqf: list[SegValMissedWaqfItem] | None = None
    false_split: list[SegValFalseSplitItem] | None = None
    unmarked_wasl: list[SegValUnmarkedWaslItem] | None = None
    boundary_adj: list[SegValBoundaryAdjItem] = Field(default_factory=list)
    cross_verse: list[SegValCrossVerseItem] = Field(default_factory=list)
    audio_bleeding: list[SegValAudioBleedingItem] = Field(default_factory=list)
    repetitions: list[SegValRepetitionItem] = Field(default_factory=list)
    muqattaat: list[SegValMuqattaatItem] = Field(default_factory=list)
    qalqala: list[SegValQalqalaItem] = Field(default_factory=list)
    basmala_amin: list[SegValBasmalaAminItem] = Field(default_factory=list)
    category_counts: dict[str, int] = Field(default_factory=dict)
    stats: SegValStats | None = None
    split_group_index: dict[str, list[str]] = Field(default_factory=dict)
    low_confidence_v2_meta: SegValProbeMeta | None = None
    hidden_pause_meta: SegValBoundaryMeta | None = None
    missed_waqf_meta: SegValBoundaryMeta | None = None
    false_split_meta: SegValBoundaryMeta | None = None
    unmarked_wasl_meta: SegValBoundaryMeta | None = None


# ===========================================================================
# /api/seg/peaks + /segment-peaks — waveform peaks
# ===========================================================================


class SegSlimPeaks(BaseModel):
    """Chapter-overview peaks in the slim int8 envelope, keyed by audio URL.

    The ``/peaks`` route emits ``read_prefetched_peaks`` verbatim: the int8
    quantized payload (``peaks_b64`` is base64 of ``n * 2`` interleaved
    min/max int8 bytes). The FE inflates ``peaks_b64`` → ``Int8Array`` once on
    receive — it does NOT receive nested ``[min, max]`` float pairs here.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    duration_ms: int
    bps: int
    q: Literal["int8"]
    n: int
    peaks_b64: str


class SegPeaksResponse(BaseModel):
    """``GET /api/seg/peaks/<reciter>?chapters=<csv>`` — slim int8 peaks per URL.

    ``complete`` is always ``true`` (no background-compute path). Missing
    chapter files drop out of ``peaks`` silently.
    """

    model_config = ConfigDict(extra="forbid")

    peaks: dict[str, SegSlimPeaks] = Field(default_factory=dict)
    complete: bool


class SegSegmentPeaksRequestItem(BaseModel):
    """One requested segment range in a ``/segment-peaks`` POST body."""

    model_config = ConfigDict(extra="forbid")

    url: str
    start_ms: int
    end_ms: int
    chapter: int | None = None
    pad_ms: int | None = None
    bps: int | None = None


class SegSegmentPeaksRequest(BaseModel):
    """``POST /api/seg/segment-peaks/<reciter>`` request body."""

    model_config = ConfigDict(extra="forbid")

    segments: list[SegSegmentPeaksRequestItem] = Field(default_factory=list)


class SegSegmentPeaks(BaseModel):
    """HD per-segment peaks computed via ffmpeg, keyed by ``"<url>:<start>:<end>"``
    (a ``:<pad>`` suffix is appended when ``pad_ms`` was requested).

    ``peaks`` are nested ``[min, max]`` float pairs (rounded).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    start_ms: int
    end_ms: int
    duration_ms: int
    peaks: list[tuple[float, float]] = Field(default_factory=list)


class SegSegmentPeaksResponse(BaseModel):
    """``POST /api/seg/segment-peaks/<reciter>`` — HD peaks keyed by range string."""

    model_config = ConfigDict(extra="forbid")

    peaks: dict[str, SegSegmentPeaks] = Field(default_factory=dict)


# Annotated discriminator alias kept available for callers that want the union
# directly (codegen consumes ``SegValAnyItem``, which wraps the same members).
SegValAnyItemAnnotated = Annotated[SegValAnyItemUnion, Field(union_mode="left_to_right")]
