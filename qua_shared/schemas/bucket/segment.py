"""Per-reciter ``detailed.json`` segment schema.

Shared source of truth for the per-segment shape inside
``reciters/<slug>/detailed.json``.
Both the offline extraction pipeline (`.local/extraction/segments/outputs.py`)
and the Inspector save flow (`inspector/services/segments/save.py`) MUST
round-trip through this model — that is the structural fix for the
writer/reader drift we discovered in migration #4 + #5 (see
``docs/reference/data-migrations.md`` §5).

``DetailedDocument`` wraps ``_meta`` + ``entries[]``; ``DetailedEntry``
wraps a per-chapter group; ``DetailedSegment`` is the atomic unit and
where 99% of the bytes live.

Extras handling: pure ``extra="forbid"`` on every model — any unexpected
field raises ``ValidationError``. Writers emit via
``model_dump(exclude_none=True)`` so optional fields with no value don't
land as ``null`` in the JSON. Snapshot fields (``chapter``, ``audio_url``,
``index_at_save``) used inside edit-history operations DO NOT live on a
persisted seg — they're modelled separately on ``SegSnapshot`` for op
payloads.

Authoritative spec: ``docs/reference/data-migrations.md`` §5.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..config.audit import Actor


class FlagFollowUp(BaseModel):
    """One append-only reply on a segment's flag thread.

    Anyone with edit access can add a follow-up; they are never edited or
    deleted in place (the whole flag disappears when the flagger clears the
    root comment). ``actor`` carries the author identity exactly like an
    edit-history batch actor — the segments read route redacts it to the
    role only for callers without ``segments.see_flagger_identity``.
    """

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(..., min_length=1)
    actor: Actor
    at_utc: str = Field(..., min_length=1)  # ISO-8601 UTC


class SegmentFlag(BaseModel):
    """A flag on a single segment: a required root comment + reply thread.

    Persisted on the segment (``DetailedSegment.flag``) and mutated through
    the normal command/save pipeline, so it shows in the History panel and
    is reversible via the patch path. It is NOT a validation category and
    never appears in filters / issue types.

    - ``comment`` — the flagger's root note (required, non-empty). Editing it
      to empty removes the whole flag (the unflag mechanism).
    - ``actor`` — who created the flag. Redacted to ``role`` only on the wire
      unless the caller holds ``segments.see_flagger_identity``.
    - ``follow_ups`` — append-only replies from any editor.
    """

    model_config = ConfigDict(extra="forbid")

    comment: str = Field(..., min_length=1)
    actor: Actor
    flagged_at_utc: str = Field(..., min_length=1)  # ISO-8601 UTC
    follow_ups: list[FlagFollowUp] = Field(default_factory=list)


class DetailedWordTiming(BaseModel):
    """One legacy/review word interval preserved inside ``detailed.json``."""

    model_config = ConfigDict(extra="forbid")

    word: str
    location: str
    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_time_range(self) -> DetailedWordTiming:
        if self.end_ms < self.start_ms:
            raise ValueError(
                f"end_ms ({self.end_ms}) must be >= start_ms ({self.start_ms})"
            )
        return self


class DetailedSegment(BaseModel):
    """Atomic seg in a chapter's ``segments`` list.

    Required (every writer always emits):
      - ``time_start`` / ``time_end`` — ms offsets within the chapter audio.
      - ``matched_ref`` — DP alignment span, e.g. ``"1:1:1-1:1:4"`` for
        canonical refs, or a special token (``"Basmala"``, ``"Isti'adha"``,
        ``"Amin"``, ``"Takbir"``, ``"Tahmeed"``, ``"Tasleem"``, ``"Sadaqa"``)
        for transition segs that get stripped by the post-pass.

    Persisted classifier optimisations (migrate_wip §2):
      - ``qalqala_letter`` — last Arabic letter when it's in the qalqala set
        (ق ط ب ج د), else ``None``. Default ``None`` for legacy data.
      - ``is_boundary_adj`` — pad-floor boundary flag (structural + phoneme
        tail). Default ``False`` for legacy data.

    Optional content fields:
      - ``confidence`` — DP alignment confidence in ``[0.0, 1.0]``. Failed
        alignments get ``0.0``.
      - ``wrap_word_ranges`` — repetition-wrap geometry. Truthy iff the seg
        contains a multi-pass recital pattern. The ``repetitions``
        validation category triggers ONLY off this field.
      - ``segment_uid`` — UUIDv7 stamped by save-flow merge / split / strip
        ops and by the ``/seg/all`` lazy backfill route. Absent on fresh
        extraction output.
      - ``word_timings`` — optional historical word intervals retained by
        imported review samples and exposed for playback highlighting.
      - ``ignored_categories`` — per-seg category-level ignore set written
        by the "ignore this issue" accordion action; consulted by
        ``services/validation/classifier.py::is_ignored_for`` to suppress
        the listed categories from the validation accordion. ``["_all"]``
        is the legacy wildcard equivalent of the retired ``ignored=True``
        boolean. See ``docs/proposals/ignored-categories-refactor.md``
        for the proposed move to a chapter-level sidecar.
      - ``ignored`` — pre-categories boolean wildcard, kept for back-compat
        read of legacy on-disk data. Save writes ``ignored_categories``
        with ``["_all"]`` instead.
    """

    model_config = ConfigDict(extra="forbid")

    # === Always-present (every writer) ===
    time_start: int = Field(..., ge=0)
    time_end: int = Field(..., ge=0)
    matched_ref: str

    # === Persisted classifier optimisations ===
    qalqala_letter: str | None = None
    is_boundary_adj: bool = False

    # === Optional content ===
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    wrap_word_ranges: list[list[str]] | None = None
    segment_uid: str | None = None
    # Optional reviewed/legacy word boundaries. Most production segment files
    # omit these, but QAB review samples carry them and the Inspector must not
    # hide them from playback review.
    word_timings: list[DetailedWordTiming] | None = None

    # === Coordinate provenance (multi-riwayah) ===
    # ``matched_ref`` above is the DELIVERY EDITION's coordinate — what the
    # Segments tab shows and edits. For Hafs that is the identity, so both
    # fields below stay ``None`` on every existing seg and the serialization
    # is byte-identical.
    #
    # Recognition and DP matching always run against the Hafs reference, so a
    # non-Hafs alignment carries its Hafs evidence here:
    #   - ``source_ref``  — the Hafs word span the matcher actually matched.
    #     The timestamps producer needs it because MFA phonemizes Hafs proxy
    #     phones; re-alignment needs it because the matcher speaks Hafs.
    #   - ``projection_support`` — the SDK's ``ProjectionGroup.support``.
    #     ``"partial"`` means this segment cuts through an N:M relation (e.g.
    #     one of Hafs ``40:26:13-14`` alone, which maps to a single target
    #     word), so only the phones that source word supports are timed.
    source_ref: str | None = None
    projection_support: Literal["full", "partial"] | None = None

    # === Per-seg "ignore this issue" state (see proposal for refactor) ===
    ignored_categories: list[str] | None = None
    ignored: bool | None = None  # legacy pre-categories wildcard

    # === Boundary annotation: wasl-connection to the next adjacent seg ===
    # Stored on the LEFT side of the connection; writers omit when False so
    # the field stays absent on every untouched seg. See ``adapters/save_payload.py``
    # for the matching omit-when-false serialization rule.
    is_wasl: bool = False

    # === Flagged-issue thread (manual "needs a second look" annotation) ===
    # Written by the flag command; ``None``/absent on every unflagged seg
    # (omitted via ``model_dump(exclude_none=True)``). Removed by clearing
    # the root comment. Never a validation category. See ``SegmentFlag``.
    flag: SegmentFlag | None = None

    @model_validator(mode="after")
    def _validate_time_range(self) -> DetailedSegment:
        if self.time_end < self.time_start:
            raise ValueError(
                f"time_end ({self.time_end}) must be >= time_start ({self.time_start})"
            )
        return self


class DetailedEntry(BaseModel):
    """Per-chapter group: ``{ref, segments[]}``.

    ``audio`` (per-chapter URL) was a duplicated source of truth with
    ``catalog/audio_manifest/<slug>.json::chapters[ch].url`` and was
    dropped in migration #5 — it is no longer accepted here.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(..., min_length=1)
    segments: list[DetailedSegment] = Field(default_factory=list)


class DetailedMeta(BaseModel):
    """The ``_meta`` block at the top of ``detailed.json``.

    Kept lean per user direction — ``audio_source`` is load-bearing for
    legacy release code;
    pad / floor fields are read by ``services/storage/data_loader.py::
    resolve_pad``; the rest is provenance.
    """

    model_config = ConfigDict(extra="forbid")

    created_at: str | None = None  # ISO-8601 timestamp; informational

    #: Inspector vocabulary slug (``qua_shared.riwayat.SUPPORTED_RIWAYAT``)
    #: naming the coordinate system every ``matched_ref`` in this document is
    #: expressed in. ``None`` means Hafs — every pre-multi-riwayah document,
    #: so no backfill is needed. A reader that renders script or resolves word
    #: counts MUST consult this rather than assuming Hafs.
    riwayah: str | None = None

    asr_model: str | None = None
    vad_model: str | None = None
    min_silence_ms: int | None = None
    min_speech_ms: int | None = None
    pad_left_ms: int | None = None
    pad_right_ms: int | None = None
    min_silence_floor_ms: int | None = None
    audio_source: str | None = None  # downstream consumers depend on this

    # Migration tracking for the pad_left/pad_right split (consumed by
    # ``services/storage/data_loader.py::resolve_pad``). Stamped by the
    # pad backfill, never by extraction directly.
    pad_ms: int | None = None  # legacy single-value pad (pre-split)
    pad_migrated_at: str | None = None
    pad_migrated_from_pad_ms: int | None = None


class DetailedDocument(BaseModel):
    """Whole ``detailed.json`` document.

    Note: the on-disk JSON key is ``_meta`` (with leading underscore) for
    historical reasons. Pydantic disallows leading-underscore field names,
    so we expose it as ``meta`` Python-side with an ``alias="_meta"`` for
    JSON I/O. Use ``model_dump(by_alias=True)`` when serialising.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    meta: DetailedMeta = Field(default_factory=DetailedMeta, alias="_meta")
    entries: list[DetailedEntry] = Field(default_factory=list)


def parse_detailed_segment(raw: dict[str, Any]) -> DetailedSegment:
    """Parse one seg dict (e.g. from inside ``entries[i].segments``).

    Pure ``extra="forbid"``: any unexpected field raises ``ValidationError``.
    New writers must serialise via ``seg.model_dump(exclude_none=True)`` to
    omit optional fields with no value (so they don't land as ``null``).
    """
    return DetailedSegment.model_validate(raw)
