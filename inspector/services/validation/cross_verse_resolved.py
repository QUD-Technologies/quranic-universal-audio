"""Resolved cross-verse items — keep a labelled split reviewable.

A cross-verse segment leaves the live ``cross_verse`` list the moment it is
split (its pieces are single-verse). The accordion still wants to show it so
the WASL/WAQF picks on the pieces can be reviewed and relabelled. Edit history
is the record: every effective ``split_segment`` op launched from the
cross-verse card (``op_context_category == "cross_verse"``) — or, for splits
made from the main list before the card existed, whose parent ``matched_ref``
spanned verses — names a split ROOT uid. When that root is still live and is
no longer cross-verse itself, it is emitted as a ``resolved`` item.

The FE expands the root through ``split_group_index`` into its pieces and
reads each boundary's state from the live ``is_wasl`` flags; nothing about
the picks is duplicated here.
"""

from __future__ import annotations

from services.activity.history_query import load_edit_history

CROSS_VERSE_CATEGORY = "cross_verse"


def _ref_is_cross_verse(ref: object) -> bool:
    """True when ``ref`` is ``a:b:c-d:e:f`` with ``b != e``."""
    if not isinstance(ref, str) or "-" not in ref:
        return False
    start, _, end = ref.partition("-")
    s_parts = start.split(":")
    e_parts = end.split(":")
    if len(s_parts) < 2 or len(e_parts) < 2:
        return False
    return s_parts[1] != e_parts[1]


def _is_split_op(op: dict) -> bool:
    return op.get("op_type") == "split_segment" or op.get("kind") == "split_segment"


def _split_root_uid(op: dict) -> str | None:
    """The parent uid of a split op, read from ``targets_before``."""
    before = op.get("targets_before") or []
    if not before or not isinstance(before[0], dict):
        return None
    uid = before[0].get("segment_uid")
    return uid if isinstance(uid, str) and uid else None


def _qualifies(op: dict) -> bool:
    """A split counts as a cross-verse split when launched from the card, or
    when its parent ref spanned verses (pre-card splits from the main list)."""
    if op.get("op_context_category") == CROSS_VERSE_CATEGORY:
        return True
    before = op.get("targets_before") or []
    parent = before[0] if before and isinstance(before[0], dict) else {}
    return _ref_is_cross_verse(parent.get("matched_ref"))


def cross_verse_split_roots(reciter: str) -> list[str]:
    """Ordered, de-duplicated root uids of every effective cross-verse split."""
    history = load_edit_history(reciter)
    roots: list[str] = []
    seen: set[str] = set()
    for batch in history.get("batches") or []:
        for op in batch.get("operations") or []:
            if not _is_split_op(op) or not _qualifies(op):
                continue
            uid = _split_root_uid(op)
            if uid and uid not in seen:
                seen.add(uid)
                roots.append(uid)
    return roots


def resolved_cross_verse_items(
    reciter: str,
    entries: list[dict],
    live_cross_verse_uids: set[str],
) -> list[dict]:
    """Build ``resolved: True`` cross-verse items for split roots still live.

    ``entries`` is the loaded ``detailed.json`` entry list; ``live_cross_verse_uids``
    the uids already emitted as unresolved items (skipped here). A root that
    was later deleted or merged away has no live seg and is dropped.
    """
    roots = cross_verse_split_roots(reciter)
    if not roots:
        return []
    wanted = {uid for uid in roots if uid not in live_cross_verse_uids}
    if not wanted:
        return []

    by_uid: dict[str, tuple[int, int, str]] = {}
    chapter_seg_idx: dict[int, int] = {}
    for entry in entries:
        chapter = int(str(entry.get("ref", "0")).split(":")[0] or 0)
        for seg in entry.get("segments", []):
            i = chapter_seg_idx.get(chapter, 0)
            chapter_seg_idx[chapter] = i + 1
            uid = seg.get("segment_uid")
            if uid in wanted:
                by_uid[uid] = (chapter, i, seg.get("matched_ref", ""))

    out: list[dict] = []
    for uid in roots:
        hit = by_uid.get(uid)
        if hit is None:
            continue
        chapter, seg_index, ref = hit
        if _ref_is_cross_verse(ref):
            # Still cross-verse (e.g. the split was undone) — the live pass
            # owns it as an unresolved item.
            continue
        out.append(
            {
                "chapter": chapter,
                "seg_index": seg_index,
                "segment_uid": uid,
                "ref": ref,
                "classified_issues": [],
                "resolved": True,
            }
        )
    return out
