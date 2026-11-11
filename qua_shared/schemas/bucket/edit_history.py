"""Per-reciter ``edit_history.jsonl`` v2 schema.

Per-batch ledger for in-app History panel browsing. v2 changes from v1:
- adds ``actor: {hf_user_id, login_at_time, role}``
- drops the file-hash chain (``file_hash_after``)
- drops the genesis record

The parser still detects + skips v0/v1 genesis records so mixed files read
without a migration script (``parse_edit_history_line`` returns ``None`` for
them), but every non-genesis batch is validated under pure ``extra="forbid"``:
an unexpected field raises ``ValidationError`` rather than being stripped.

Spec: docs/planning/inspector-deploy/v2/inspector-deployment-plan.md §7 +
inspector-data-storage.md §8.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config.audit import Actor


class EditOpPatch(BaseModel):
    """Forward-change patch envelope attached to an op at finalize time.

    Structural mirror of the ``SegmentPatch`` dataclass in
    ``inspector/domain/command.py`` and the FE ``EditOpPatch`` interface in
    ``inspector/frontend/src/lib/types/view-models.ts``. Produced by the FE
    ``applyCommand`` round-trip; consumed by the undo path
    (``apply_inverse_patch`` reads ``op["patch"]``).

    ``before`` / ``after`` are full segment-dict snapshots; ``removedIds`` /
    ``insertedIds`` carry the segment UIDs the command removed/inserted;
    ``affectedChapterIds`` is the set of chapter numbers whose id ordering
    changed (ints — what the FE sends and the undo path compares against the
    batch chapter set).
    """

    model_config = ConfigDict(extra="forbid")

    before: list[dict[str, Any]] = Field(default_factory=list)
    after: list[dict[str, Any]] = Field(default_factory=list)
    removedIds: list[str] = Field(default_factory=list)
    insertedIds: list[str] = Field(default_factory=list)
    affectedChapterIds: list[int] = Field(default_factory=list)


class EditOperation(BaseModel):
    """One operation in a batch. The save flow owns the operation vocabulary
    (trim, split, merge, delete, etc.) and stores per-op payloads keyed by
    ``kind`` (user-driven) or ``op_type`` (pipeline-driven, written by
    ``.local/extraction/segments/post_passes.py``).

    Migration #5: pipeline ops carry ``op_type`` + ``fix_kind`` (no
    ``kind`` — that's a user-edit-only field set by the FE command store).
    ``kind`` is therefore optional. At least one of ``kind`` /
    ``op_type`` must be present for the op to be meaningful.

    ``targets_before`` / ``targets_after`` carry seg snapshots — list of
    dicts, not validated against ``DetailedSegment`` because snapshots
    intentionally carry extra fields (``chapter``, ``audio_url``,
    ``index_at_save``) that don't live on persisted segs.

    ``patch`` is the forward-change envelope the save flow persists on every
    op (``_ensure_patch_on_ops``) and the undo path reverses.
    ``op_context_category`` is the validation category the edit was launched
    from; ``build_resolved_by_edit_index`` reads it to suppress re-flagging.
    Both are live fields, written and read by the app — NOT dead.

    The user-edit save flow (``apply-command.ts::_baseOperation``) also stamps a
    handful of FE working/presentation fields that the Inspector save path
    persists verbatim and the History panel reads back: ``merge_direction``
    (``'prev'``/``'next'`` — drives the merge-highlight point in
    ``EditChainRow``/``HistoryOp``), ``snapshots`` (the ``{before, after}``
    mirror of ``targets_*`` that ``utils/history/items.ts`` reads for the
    ``is_wasl`` waqf pill), plus the ``type`` op alias, the ``command`` payload
    and the ``targetSegmentIndex`` locator. They are declared optional so a
    real user-edit op round-trips under pure ``extra="forbid"`` (only the
    pipeline writer in ``post_passes.py`` round-trips through this model at
    write time; the Inspector save persists the raw op). Pipeline ops leave
    them ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    op_id: str = Field(..., min_length=1)
    kind: str | None = None  # user-driven; absent for pipeline ops
    op_type: str | None = None  # pipeline-driven; absent for user ops

    fix_kind: str | None = None  # only set by pipeline auto-fix ops
    op_context_category: str | None = None  # validation category the edit came from
    patch: EditOpPatch | None = None  # forward-change envelope for undo
    targets_before: list[dict[str, Any]] = Field(default_factory=list)
    targets_after: list[dict[str, Any]] = Field(default_factory=list)

    # FE user-edit working/presentation fields, persisted verbatim by the
    # Inspector save flow and read back by the History panel (see class doc).
    type: str | None = None  # user-edit op alias (split/merge/delete/…)
    merge_direction: str | None = None  # 'prev'/'next' on merge ops
    snapshots: dict[str, Any] | None = None  # {before, after} mirror of targets_*
    targetSegmentIndex: dict[str, Any] | None = None  # {chapter, index} locator
    command: dict[str, Any] | None = None  # raw FE command payload
    affected_chapters: list[int] | None = None


class EditHistoryBatch(BaseModel):
    """One JSONL line in ``edit_history.jsonl`` — a batch of operations.

    Migration #5 reality-check: both writers (Inspector save +
    ``.local/extraction/segments/post_passes.py``) stamp the timestamp as
    ``saved_at_utc`` (string). The legacy ``ts`` field has been retired
    from the schema; ``saved_at_utc`` is the canonical declared field.

    ``chapter`` (single) is written by Inspector save (one batch per
    chapter save). ``chapters`` (list) is written by the pipeline strip-
    specials post-pass (one batch can span multiple chapters when audio
    contains Basmala/Isti'adha at chapter starts). Exactly one of the
    two is present; both are tolerated for forward-compat.

    ``schema_version`` defaults to ``1`` matching the literal both writers
    emit; readers can bump to ``2`` later in a separate migration once
    both writers are updated together.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    batch_id: str = Field(..., min_length=1)
    saved_at_utc: str | None = None  # ISO-8601 UTC; canonical Migration #5
    actor: Actor | None = None
    operations: list[EditOperation] = Field(default_factory=list)

    # Per-batch chapter scope. Exactly one of these is set by a writer:
    #   - Inspector save → ``chapter`` (single, per-chapter save)
    #   - Pipeline strip-specials → ``chapters`` (list, multi-chapter run)
    chapter: int | None = None
    chapters: list[int] = Field(default_factory=list)

    # Pipeline-stamped: ``strip_specials`` / ``waqf_sakt`` etc.; user-edit
    # batches leave this None.
    batch_type: str | None = None

    # Optional cross-batch fields used by undo / revert filtering.
    reverts_batch_id: str | None = None
    reverts_op_ids: list[str] = Field(default_factory=list)

    # Inspector save legacy: pre-#5 batches embedded a snapshot of the
    # validation summary before/after the save. Migration #5 stopped
    # emitting these (the FE recomputes from live segs). Kept optional
    # so on-disk legacy batches parse.
    validation_summary_before: dict[str, Any] = Field(default_factory=dict)
    validation_summary_after: dict[str, Any] = Field(default_factory=dict)


def parse_edit_history_line(raw: str | bytes) -> EditHistoryBatch | None:
    """Parse one ``edit_history.jsonl`` line, tolerating v1 + v2 records.

    Returns ``None`` for legacy v1 genesis records (which carry neither
    a ``batch_id`` nor any user-visible operations). The detector matches
    both the v0 (``type=="genesis"``) and v1 (``record_type=="genesis"``)
    sentinels, plus the structural ``"batch_id" not in obj`` fallback for
    pre-batch_id record formats.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    text = raw.strip()
    if not text:
        return None
    obj = json.loads(text)

    if obj.get("type") == "genesis" or obj.get("record_type") == "genesis" or "batch_id" not in obj:
        return None

    return EditHistoryBatch.model_validate(obj)
