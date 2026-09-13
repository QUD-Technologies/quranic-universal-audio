"""Which segment edit operations change the generated timestamps artifact.

Single source of truth for "does this edit make the generated timestamps
stale?". An edit affects timestamps when it moves a segment boundary
(trim/split/merge/delete) OR changes which ayah a segment maps to
(edit_reference/auto_fix_missing_word) OR changes the waṣl flag
(set_is_wasl) — the shard builder joins adjacent occurrences into one reading
while the preceding one carries ``wasl``, so flipping the flag changes the
phonemized chain. Pure annotations/comments (confirm_reference, ignore_issue,
flag_segment) never touch the artifact and are intentionally excluded.

The op_type vocabulary is fixed by ``OP_TYPE_BY_COMMAND`` in the FE
``tabs/segments/domain/apply-command.ts`` (auto-split has no distinct op_type
— it commits as a normal ``split`` → ``split_segment``). The FE mirrors this
set with a lockstep comment; keep them in step.

Consumed by ``services/segments/ts_staleness.py`` (the Releases-tab TS-stale
signal) and ``services/segments/history_tiers.py`` (the history "pending
regen" highlight).
"""

from __future__ import annotations

#: op_type / kind values whose presence in a saved batch means the generated
#: timestamps no longer reflect the segments. = structural boundary edits ∪
#: reference-mapping edits ∪ the waṣl flag.
TS_AFFECTING_OP_TYPES: frozenset[str] = frozenset(
    {
        "trim_segment",
        "split_segment",
        "merge_segments",
        "delete_segment",
        "edit_reference",
        "auto_fix_missing_word",
        "set_is_wasl",
    }
)


def affects_timestamps(op_type: str | None) -> bool:
    """True iff a single op's ``op_type`` (or ``kind``) alters the timestamps."""
    return op_type in TS_AFFECTING_OP_TYPES


def batch_affects_timestamps(batch: dict) -> bool:
    """True iff any operation in an edit_history batch alters the timestamps.

    Checks both ``op_type`` (set on every op) and ``kind`` (user-edit ops also
    carry it) so a record that labels the op under either key is caught.
    """
    for op in batch.get("operations") or []:
        if affects_timestamps(op.get("op_type")) or affects_timestamps(op.get("kind")):
            return True
    return False
