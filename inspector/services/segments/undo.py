"""Undo operations: batch reversal, snapshot verification, segment restoration.

No Flask imports -- all functions accept parameters and return plain dicts.
"""

from datetime import UTC, datetime

from constants import HISTORY_SCHEMA_VERSION
from domain.command import apply_inverse_patch
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from qua_shared.schemas import Actor
from services.activity.history_query import parse_history_for_reciter
from services.reference.delivery_edition import sdk_riwayah_for
from services.segments.save import persist_detailed
from services.segments.stamping import stamp_segment
from services.storage import cache, data_dir
from services.storage.data_loader import load_detailed
from utils.references import chapter_from_ref
from utils.uuid7 import uuid7


def find_segment_by_uid(entries: list[dict], uid: str, chapter_set: set[int]):
    """Find a segment by segment_uid within entries matching *chapter_set*.

    Returns ``(entry, seg_index, segment)`` or ``None``.
    """
    for entry in entries:
        ch = chapter_from_ref(entry["ref"])
        if ch not in chapter_set:
            continue
        for i, seg in enumerate(entry.get("segments", [])):
            if seg.get("segment_uid") == uid:
                return (entry, i, seg)
    return None


def find_entry_for_insert(entries: list[dict], snap: dict, chapter_set: set[int]):
    """Find the correct entry to insert a restored segment into.

    For by_surah: a single entry per chapter, so the first chapter match is
    correct. For by_ayah: multiple entries per chapter (one per ayah) —
    match by the snapshot's ``entry_ref`` (set by ``snapshotSeg`` on every
    new save) against ``entry["ref"]``. Legacy snapshots that predate
    ``entry_ref`` fall back to the chapter-membership match.
    """
    snap_ref = snap.get("entry_ref") or ""
    if snap_ref:
        for entry in entries:
            if entry.get("ref") == snap_ref:
                return entry
    for entry in entries:
        ch = chapter_from_ref(entry["ref"])
        if ch in chapter_set:
            return entry
    return None


def snap_to_segment(snap: dict) -> dict:
    """Convert a snapshot to a segment dict for insertion."""
    seg = {
        "segment_uid": snap.get("segment_uid", uuid7()),
        "time_start": snap["time_start"],
        "time_end": snap["time_end"],
        "matched_ref": snap.get("matched_ref", ""),
        "confidence": snap.get("confidence", 0),
    }
    if snap.get("wrap_word_ranges"):
        seg["wrap_word_ranges"] = snap["wrap_word_ranges"]
    if snap.get("ignored_categories"):
        seg["ignored_categories"] = list(snap["ignored_categories"])
    return seg


def _restore_ignored_categories(seg: dict, snap: dict) -> None:
    """Restore per-category ignore markers from a snapshot."""
    if snap.get("ignored_categories"):
        seg["ignored_categories"] = list(snap["ignored_categories"])
    else:
        seg.pop("ignored_categories", None)
        seg.pop("ignored", None)


def verify_segment_matches_snapshot(seg: dict, snap: dict) -> str | None:
    """Check if a current segment matches its expected snapshot state.

    Returns an error message if there's a conflict, ``None`` if OK.
    """
    uid = snap.get("segment_uid", "")
    if seg.get("time_start") != snap.get("time_start") or seg.get("time_end") != snap.get(
        "time_end"
    ):
        return (
            f"Segment {uid} times changed since this save "
            f"(expected {snap.get('time_start')}-{snap.get('time_end')}, "
            f"found {seg.get('time_start')}-{seg.get('time_end')})"
        )
    if seg.get("matched_ref", "") != snap.get("matched_ref", ""):
        return f"Segment {uid} reference changed since this save"
    return None


# ---------------------------------------------------------------------------
# Shared find + verify helper
# ---------------------------------------------------------------------------


def _find_and_verify(
    entries: list[dict], snap_after: dict, chapter_set: set[int]
) -> tuple[dict, int, dict]:
    """Look up a segment by UID and verify it matches *snap_after*.

    Returns ``(entry, index, seg)`` or raises ``ValueError`` on conflict.
    """
    uid = snap_after.get("segment_uid", "")
    found = find_segment_by_uid(entries, uid, chapter_set)
    if not found:
        raise ValueError(f"Segment {uid} not found — it may have been deleted by a later edit")
    entry, idx, seg = found
    conflict = verify_segment_matches_snapshot(seg, snap_after)
    if conflict:
        raise ValueError(conflict)
    return entry, idx, seg


# ---------------------------------------------------------------------------
# Per-op-type branch helpers
# ---------------------------------------------------------------------------


def _reverse_trim(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse a trim_segment or auto_fix_missing_word operation."""
    before = op.get("targets_before", [])
    after = op.get("targets_after", [])
    if not after or not before:
        return
    entry, idx, seg = _find_and_verify(entries, after[0], chapter_set)
    snap_before = before[0]
    seg["time_start"] = snap_before["time_start"]
    seg["time_end"] = snap_before["time_end"]
    seg["matched_ref"] = snap_before.get("matched_ref", "")
    seg["confidence"] = snap_before.get("confidence", 0)
    _restore_ignored_categories(seg, snap_before)
    entry["segments"].sort(key=lambda s: s["time_start"])


def _reverse_split(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse a split_segment operation."""
    before = op.get("targets_before", [])
    after = op.get("targets_after", [])
    if len(after) < 2 or not before:
        return
    found_list = []
    for snap_after in after:
        uid = snap_after.get("segment_uid", "")
        found = find_segment_by_uid(entries, uid, chapter_set)
        if not found:
            raise ValueError(f"Segment {uid} not found — it may have been modified by a later edit")
        conflict = verify_segment_matches_snapshot(found[2], snap_after)
        if conflict:
            raise ValueError(conflict)
        found_list.append(found)
    entry = found_list[0][0]
    indices = sorted([f[1] for f in found_list], reverse=True)
    for idx in indices:
        entry["segments"].pop(idx)
    restored = snap_to_segment(before[0])
    entry["segments"].append(restored)
    entry["segments"].sort(key=lambda s: s["time_start"])


def _reverse_merge(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse a merge_segments or waqf_sakt operation."""
    before = op.get("targets_before", [])
    after = op.get("targets_after", [])
    if not after or not before:
        return
    entry, idx, _ = _find_and_verify(entries, after[0], chapter_set)
    entry["segments"].pop(idx)
    for snap_before in before:
        restored = snap_to_segment(snap_before)
        entry["segments"].append(restored)
    entry["segments"].sort(key=lambda s: s["time_start"])


def _reverse_delete(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse a delete_segment operation."""
    before = op.get("targets_before", [])
    if not before:
        return
    snap_before = before[0]
    entry = find_entry_for_insert(entries, snap_before, chapter_set)
    if not entry:
        raise ValueError("Could not find entry to restore deleted segment into")
    restored = snap_to_segment(snap_before)
    entry["segments"].append(restored)
    entry["segments"].sort(key=lambda s: s["time_start"])


def _reverse_ref_edit(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse an edit_reference, confirm_reference, or remove_sadaqa operation."""
    before = op.get("targets_before", [])
    after = op.get("targets_after", [])
    if not after or not before:
        return
    _, _, seg = _find_and_verify(entries, after[0], chapter_set)
    snap_before = before[0]
    seg["matched_ref"] = snap_before.get("matched_ref", "")
    seg["confidence"] = snap_before.get("confidence", 0)
    _restore_ignored_categories(seg, snap_before)


def _reverse_ignore(entries: list[dict], op: dict, chapter_set: set[int]) -> None:
    """Reverse an ignore_issue operation."""
    before = op.get("targets_before", [])
    after = op.get("targets_after", [])
    if not after or not before:
        return
    _, _, seg = _find_and_verify(entries, after[0], chapter_set)
    snap_before = before[0]
    seg["confidence"] = snap_before.get("confidence", 0)
    _restore_ignored_categories(seg, snap_before)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _reverse_via_patch(
    entries: list[dict], op: dict, chapter_set: set[int], riwayah: str = DEFAULT_SDK_RIWAYAH
) -> None:
    """Apply the inverse of an op by running the patch in reverse.

    Validates that the patch's affectedChapterIds are within the batch's
    chapter_set. A mismatch indicates a malformed patch claiming chapters
    outside the batch scope; raise ValueError so the caller can surface it.
    """
    import logging

    patch = op["patch"]
    patch_chapters = set(patch.get("affectedChapterIds") or [])
    if patch_chapters and chapter_set and not patch_chapters.issubset(chapter_set):
        outside = patch_chapters - chapter_set
        logging.getLogger(__name__).warning(
            "patch affectedChapterIds %s outside batch chapter_set %s — skipping op",
            outside,
            chapter_set,
        )
        raise ValueError(f"patch claims chapters {outside} outside the batch scope {chapter_set}")
    apply_inverse_patch(entries, patch, riwayah)


def apply_reverse_op(
    entries: list[dict], op: dict, chapter_set: set[int], riwayah: str = DEFAULT_SDK_RIWAYAH
) -> None:
    """Apply the reverse of a single operation.  Raises ``ValueError`` on conflict.

    ``riwayah`` only reaches the patch path, which is the one that re-derives a
    snapshot's ``matched_text`` — every other reverse restores stored text.
    """
    if "patch" in op:
        _reverse_via_patch(entries, op, chapter_set, riwayah)
        return
    op_type = op.get("op_type", "")
    if op_type in ("trim_segment", "auto_fix_missing_word"):
        _reverse_trim(entries, op, chapter_set)
    elif op_type == "split_segment":
        _reverse_split(entries, op, chapter_set)
    elif op_type in ("merge_segments", "waqf_sakt"):
        _reverse_merge(entries, op, chapter_set)
    elif op_type == "delete_segment":
        _reverse_delete(entries, op, chapter_set)
    elif op_type in ("edit_reference", "confirm_reference", "remove_sadaqa"):
        _reverse_ref_edit(entries, op, chapter_set)
    elif op_type == "ignore_issue":
        _reverse_ignore(entries, op, chapter_set)


# ---------------------------------------------------------------------------
# Internal helpers (used by undo_batch / undo_ops)
# ---------------------------------------------------------------------------


def _get_affected_chapters(batch: dict) -> set[int]:
    """Extract all affected chapters from a batch record."""
    affected = set()
    ch = batch.get("chapter")
    if ch is not None:
        affected.add(ch)
    chs = batch.get("chapters")
    if chs:
        affected.update(chs)
    return affected


def _restamp(entries: list[dict], chapters: set[int], riwayah: str) -> None:
    """Re-derive the backend-owned stamped fields on every entry an undo touched.

    An undo is a write like any other, and two of its paths move a segment's
    coordinates: ``_reverse_ref_edit`` rewrites ``matched_ref`` outright, and
    the patch path replaces the whole segment dict with the frontend's
    before-snapshot. That snapshot carries only what the editor can change --
    ``snapshotSeg`` never sees ``source_ref``, ``projection_support`` or
    ``qalqala_letter``, because none of them are on the wire model -- so
    restoring it drops them.

    For a projected delivery that is not cosmetic: the timestamps engine refuses
    a run whose projected segments have no ``source_ref``
    (``assert_projected_segments_are_sourced``), so one undo would block the
    next alignment until every touched segment was re-saved by hand.
    """
    for entry in entries:
        if chapters and chapter_from_ref(entry.get("ref", "")) not in chapters:
            continue
        for seg in entry.get("segments", []):
            stamp_segment(seg, riwayah)


def _append_revert_record(
    reciter: str,
    target_batch_id: str,
    chapter,
    chapters,
    *,
    actor: Actor,
    reverts_op_ids: list[str] | None = None,
) -> None:
    """Append a revert record to edit_history.jsonl via the storage backend."""
    revert = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "batch_id": uuid7(),
        "reverts_batch_id": target_batch_id,
        "chapter": chapter,
        "saved_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "operations": [],
        "actor": actor.model_dump(mode="json"),
    }
    if reverts_op_ids:
        revert["reverts_op_ids"] = reverts_op_ids
    if chapters:
        revert["chapters"] = chapters
    data_dir.append_edit_history(reciter, revert)
    # Keep the cached parsed list in step with disk so the next reader
    # doesn't re-parse the JSONL just to see this revert.
    cache.append_history_batch(reciter, revert)


# ---------------------------------------------------------------------------
# Public undo entry points
# ---------------------------------------------------------------------------


def undo_batch(reciter: str, target_batch_id: str, *, actor: Actor) -> dict | tuple:
    """Undo a specific saved batch.  Returns result dict or (error_dict, status).

    ``actor`` (kw-only) is stamped on the revert record so the History
    panel can attribute who undid the batch (separate from who originally
    saved it).
    """
    all_records = parse_history_for_reciter(reciter)
    if not all_records:
        return {"error": "No edit history found"}, 404

    matching = [
        rec
        for rec in all_records
        if rec.get("batch_id") == target_batch_id and not rec.get("reverts_batch_id")
    ]
    if not matching:
        return {"error": "Batch not found"}, 404

    matching.sort(key=lambda r: (r.get("saved_at_utc") or "", id(r)))
    target_batch = matching[0]

    if target_batch.get("reverts_batch_id"):
        return {"error": "Cannot undo a revert record"}, 400

    already_reverted = {
        r.get("reverts_batch_id")
        for r in all_records
        if r.get("reverts_batch_id") and not r.get("reverts_op_ids")
    }
    if target_batch_id in already_reverted:
        return {"error": "This batch has already been undone"}, 400

    operations: list = []
    for rec in matching:
        operations.extend(rec.get("operations") or [])
    if not operations:
        return {"error": "Batch has no operations to undo"}, 400

    # Skip ops already individually undone
    per_op_reverted = set()
    for r in all_records:
        if r.get("reverts_batch_id") == target_batch_id and r.get("reverts_op_ids"):
            per_op_reverted.update(r["reverts_op_ids"])
    if per_op_reverted:
        operations = [op for op in operations if op.get("op_id") not in per_op_reverted]
        if not operations:
            return {
                "error": "All operations in this batch have already been individually undone"
            }, 400

    entries = load_detailed(reciter)
    if not entries:
        return {"error": "Reciter data not found"}, 404

    meta = cache.get_seg_meta(reciter)
    riwayah = sdk_riwayah_for(reciter)
    affected_chapters: set[int] = set()
    for rec in matching:
        affected_chapters.update(_get_affected_chapters(rec))

    # ``entries`` IS the process cache — ``load_detailed`` returns
    # ``cache.get_seg_cache(reciter)`` by reference — and the reverse ops below
    # mutate it before anything is written. An undo that stops partway (a patch
    # claiming chapters outside the batch, a ref ``_restamp`` refuses) would
    # otherwise leave those never-persisted changes visible to every later
    # request, and the next save would write them out. Same guard as the save
    # path; see ``segments/save.py``.
    persisted = False
    try:
        try:
            for op in reversed(operations):
                apply_reverse_op(entries, op, affected_chapters, riwayah)
        except ValueError as e:
            return {"error": str(e)}, 409
        # Outside the inner ``except``: a ``RefNotInEdition`` from here is a 400
        # about the ref, not a 409 about the batch, and a write-time
        # ValidationError is a fault rather than a conflict.
        _restamp(entries, affected_chapters, riwayah)
        persist_detailed(reciter, meta, entries, affected_chapters)
        persisted = True
    finally:
        if not persisted:
            cache.invalidate_seg_caches(reciter)

    ch_union = sorted(affected_chapters)
    _append_revert_record(
        reciter,
        target_batch_id,
        ch_union[0] if len(ch_union) == 1 else None,
        ch_union if len(ch_union) > 1 else None,
        actor=actor,
    )

    # Surgical eviction. Derived edit-history indices (split-group, resolved-
    # by-edit) can't be modelled incrementally on revert — pop them so the
    # next reader rebuilds from the cached batch list (in-memory walk, no
    # I/O). Auto-split only needs eviction when the reverted ops moved uids
    # in/out of the seg set (split / merge / auto-fix / delete).
    cache.pop_seg_caches_affected_by_segment_edit(reciter)
    cache.pop_seg_split_group_index(reciter)
    if cache.batch_changes_segment_set({"operations": operations}):
        cache.pop_seg_auto_split(reciter)
    return {"ok": True, "operations_reversed": len(operations)}


def undo_ops(
    reciter: str,
    target_batch_id: str,
    requested_op_ids: set[str],
    *,
    actor: Actor,
) -> dict | tuple:
    """Undo specific operations within a saved batch.

    Returns result dict or ``(error_dict, status)``.
    """
    all_records = parse_history_for_reciter(reciter)
    if not all_records:
        return {"error": "No edit history found"}, 404

    matching = [
        rec
        for rec in all_records
        if rec.get("batch_id") == target_batch_id and not rec.get("reverts_batch_id")
    ]
    if not matching:
        return {"error": "Batch not found"}, 404

    target_batch = matching[0]

    if target_batch.get("reverts_batch_id"):
        return {"error": "Cannot undo operations in a revert record"}, 400

    fully_reverted = {
        r.get("reverts_batch_id")
        for r in all_records
        if r.get("reverts_batch_id") and not r.get("reverts_op_ids")
    }
    if target_batch_id in fully_reverted:
        return {"error": "This batch has already been fully undone"}, 400

    already_reverted_ops = set()
    for r in all_records:
        if r.get("reverts_batch_id") == target_batch_id and r.get("reverts_op_ids"):
            already_reverted_ops.update(r["reverts_op_ids"])

    already_undone = requested_op_ids & already_reverted_ops
    if already_undone:
        return {"error": f"Operation(s) already undone: {', '.join(already_undone)}"}, 400

    all_operations: list = []
    for rec in matching:
        all_operations.extend(rec.get("operations") or [])

    all_op_ids = {op.get("op_id") for op in all_operations}
    missing = requested_op_ids - all_op_ids
    if missing:
        return {"error": f"Operation(s) not found in batch: {', '.join(missing)}"}, 404

    ops_to_undo = [op for op in all_operations if op.get("op_id") in requested_op_ids]

    entries = load_detailed(reciter)
    if not entries:
        return {"error": "Reciter data not found"}, 404

    meta = cache.get_seg_meta(reciter)
    riwayah = sdk_riwayah_for(reciter)
    affected_chapters: set[int] = set()
    for rec in matching:
        affected_chapters.update(_get_affected_chapters(rec))

    # ``entries`` IS the process cache — ``load_detailed`` returns
    # ``cache.get_seg_cache(reciter)`` by reference — and the reverse ops below
    # mutate it before anything is written. An undo that stops partway (a patch
    # claiming chapters outside the batch, a ref ``_restamp`` refuses) would
    # otherwise leave those never-persisted changes visible to every later
    # request, and the next save would write them out. Same guard as the save
    # path; see ``segments/save.py``.
    persisted = False
    try:
        try:
            for op in reversed(ops_to_undo):
                apply_reverse_op(entries, op, affected_chapters, riwayah)
        except ValueError as e:
            return {"error": str(e)}, 409
        # Outside the inner ``except``: a ``RefNotInEdition`` from here is a 400
        # about the ref, not a 409 about the batch, and a write-time
        # ValidationError is a fault rather than a conflict.
        _restamp(entries, affected_chapters, riwayah)
        persist_detailed(reciter, meta, entries, affected_chapters)
        persisted = True
    finally:
        if not persisted:
            cache.invalidate_seg_caches(reciter)

    ch_union = sorted(affected_chapters)
    _append_revert_record(
        reciter,
        target_batch_id,
        ch_union[0] if len(ch_union) == 1 else None,
        ch_union if len(ch_union) > 1 else None,
        actor=actor,
        reverts_op_ids=list(requested_op_ids),
    )

    # Surgical eviction (see undo_batch for rationale).
    cache.pop_seg_caches_affected_by_segment_edit(reciter)
    cache.pop_seg_split_group_index(reciter)
    if cache.batch_changes_segment_set({"operations": ops_to_undo}):
        cache.pop_seg_auto_split(reciter)
    return {"ok": True, "operations_reversed": len(ops_to_undo)}
