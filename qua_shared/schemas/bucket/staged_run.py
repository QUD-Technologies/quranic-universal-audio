"""Readers for a staged generation run — the batch pipeline's handoff to promote.

One run stages a ``candidates/<chapter>.json`` per chapter, one ``events.json``,
and a ``manifest.json`` naming and hashing every staged file.
``scripts/bucket/promote_run.py`` reads exactly these shapes and writes
``reciters/<slug>/``.

The producer's models live in the monorepo (``qua_contracts.generation``), which
cannot be installed here — so these are mirrors, not imports. They declare only
what promote consumes and are ``extra="ignore"``, which is what lets the producer
add a field without a coordinated release here. The three golden files under
``fixtures/`` are byte-identical copies of the producer's own, and
``qua_shared/tests/test_staged_run.py`` is the executable half of the contract.

A pre-stamp event cannot validate as an ``EditOperation``: it carries no ``op_id``
and a top-level ``chapter`` that model forbids. ``stamp_operation`` is the one
conversion, and it builds snapshot **dicts** — the type ``EditOperation``'s
``targets_before`` / ``targets_after`` actually declare, because a snapshot carries
``chapter`` / ``audio_url`` / ``index_at_save`` that ``DetailedSegment`` forbids.
``PipelineAuditEvent`` is a read-only view: nothing is ever assigned onto it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .edit_history import EditOperation

# Every published pipeline operation is an auto-fix, matching the offline writer.
_PIPELINE_FIX_KIND = "auto_fix"


class ArtifactEntryDoc(BaseModel):
    """One staged file: its run-relative path, digest and size."""

    model_config = ConfigDict(extra="ignore")

    path: str
    sha256: str
    bytes: int


class RunInputsDoc(BaseModel):
    """What the run was asked to produce, and where its audio came from.

    ``audio_source`` is the intake manifest's ``_meta.source`` and lands in the
    promoted ``_meta`` block; it is ``None`` when the input carried none.

    ``riwayah`` is the producer's **SDK** slug (``warsh``, not ``warsh_an_nafi``)
    and names the coordinate system every staged ``matched_ref`` is written in.
    A run staged before multi-riwayah names none, which is Hafs.
    """

    model_config = ConfigDict(extra="ignore")

    slug: str
    chapters: list[int]
    audio_source: str | None = None
    riwayah: str = "hafs"


class RunManifestDoc(BaseModel):
    """A staged run's identity, its model revisions and its files.

    ``profile`` stays opaque — promote reads ``profile["segmentation"]`` for the
    ``_meta`` knobs and never interprets the rest. ``required`` holds the concrete
    relative paths promote must find present and hash-matched before it writes
    anything; ``model_revisions`` carries the ``"asr"`` and ``"vad"`` identities the
    ``_meta`` projection publishes, and its other keys stay opaque here.
    """

    model_config = ConfigDict(extra="ignore")

    run_id: str
    slug: str
    source_commit: str
    created_at: str
    profile: Any
    profile_sha256: str
    model_revisions: dict[str, str]
    inputs: RunInputsDoc
    artifacts: list[ArtifactEntryDoc]
    required: list[str]


class CandidateEntryDoc(BaseModel):
    """One chapter-local ``{ref, segments[]}`` group.

    Segments stay dicts: promote validates each as a ``DetailedSegment``, whose
    field names and integer-millisecond units the producer already writes, so the
    two validations remain one validation with no rename step between them.
    """

    model_config = ConfigDict(extra="ignore")

    ref: str
    segments: list[dict[str, Any]]


class ChapterCandidateDoc(BaseModel):
    """Every candidate segment for one chapter, plus the audio it was cut from.

    ``riwayah`` repeats the run's SDK slug on each candidate file. Promote checks
    the two agree: a candidate copied in from another run is otherwise
    indistinguishable, and its refs would be published under the wrong edition.
    """

    model_config = ConfigDict(extra="ignore")

    chapter: int
    entries: list[CandidateEntryDoc]
    source_url: str
    source_offset_ms: int
    trim_span_ms: tuple[int, int] | None
    riwayah: str = "hafs"


class PipelineSegSnapshot(BaseModel):
    """One segment as a pre-stamp audit event saw it, in chapter-local ms.

    ``index_at_save`` is the position the operation recorded and is republished
    verbatim. ``final_index`` is the same row's position after the specials strip
    — carried only on a waqf-sakt ``targets_after`` snapshot, consumed by promote
    to find the persisted row, and never published.
    """

    model_config = ConfigDict(extra="ignore")

    index_at_save: int
    final_index: int | None = None
    time_start: int
    time_end: int
    matched_ref: str
    confidence: float


class PipelineAuditEvent(BaseModel):
    """A pre-stamp pipeline operation: a waqf-sakt merge or a specials delete.

    Read-only. The segment UIDs promote derives reach the published operation
    through ``stamp_operation``'s arguments, never by assignment onto this model.
    """

    model_config = ConfigDict(extra="ignore")

    kind: Literal["waqf_sakt", "delete_segment"]
    chapter: int
    targets_before: list[PipelineSegSnapshot] = Field(default_factory=list)
    targets_after: list[PipelineSegSnapshot] = Field(default_factory=list)


def _snapshot_dict(
    snap: PipelineSegSnapshot,
    chapter: int,
    audio_url: str,
    segment_uid: str | None,
) -> dict[str, Any]:
    """Build one published snapshot dict — the offline ``seg_snapshot`` keys, in order."""
    return {
        "segment_uid": segment_uid,
        "index_at_save": snap.index_at_save,
        "chapter": chapter,
        "audio_url": audio_url,
        "time_start": snap.time_start,
        "time_end": snap.time_end,
        "matched_ref": snap.matched_ref,
        "confidence": snap.confidence,
    }


def stamp_operation(
    ev: PipelineAuditEvent,
    *,
    op_id: str,
    audio_url: str,
    after_uid: str | None = None,
    before_uids: list[str | None] | None = None,
) -> dict[str, Any]:
    """Stamp a pre-stamp event into one published ``EditOperation`` dict.

    Derives nothing: promote derives the waqf UID once — for both the persisted
    row and this operation — and hands it in as ``after_uid``. ``before_uids``
    carries the per-snapshot UIDs for a ``delete_segment`` event; a waqf event's
    ``targets_before`` snapshots get none, exactly as the offline writer published
    them. The event's ``chapter`` lands on each snapshot (``EditOperation`` is
    ``extra="forbid"`` and declares none) and ``final_index`` is never published.

    Validating through ``EditOperation`` here means a bad stamp raises in this
    helper rather than at the JSONL append.
    """
    if before_uids is not None and len(before_uids) != len(ev.targets_before):
        raise ValueError(
            f"before_uids has {len(before_uids)} entries for "
            f"{len(ev.targets_before)} targets_before snapshots"
        )

    op = {
        "op_id": op_id,
        "op_type": ev.kind,
        "fix_kind": _PIPELINE_FIX_KIND,
        "targets_before": [
            _snapshot_dict(snap, ev.chapter, audio_url, before_uids[i] if before_uids else None)
            for i, snap in enumerate(ev.targets_before)
        ],
        "targets_after": [
            _snapshot_dict(snap, ev.chapter, audio_url, after_uid if i == 0 else None)
            for i, snap in enumerate(ev.targets_after)
        ],
    }
    return EditOperation.model_validate(op).model_dump(exclude_none=True)
