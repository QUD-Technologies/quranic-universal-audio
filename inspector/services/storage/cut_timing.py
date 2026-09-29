"""Measured silence on Low Confidence Waqf cuts, from ``cut_timing_v1.json``.

The timing engine's cut-timing pass aligns each proposed stop with a seeded psil
(stop form before, start form after) and reports the silence it found:
``by_uid[uid].joins[] = {kind, after_ref, cursor_ms, silence_start_ms,
silence_end_ms, source}``. A proposal's cut takes the pass's cursor (the middle
of that silence) and carries the span, so a WAQF answer can split with a gap.
Joins are matched to cuts by ``after_ref``; a cut the pass did not time keeps
its sidecar cursor and no span.
"""

from __future__ import annotations

import copy

PROPOSAL = "proposal"
TIMED_SOURCES = frozenset({"psil", "energy"})


def _after_ref(cut: dict) -> str | None:
    for block in (cut.get("evidence") or {}).values():
        if isinstance(block, dict) and block.get("after_ref"):
            return str(block["after_ref"])
    return None


def _timed(join: dict) -> tuple[int, int, int] | None:
    try:
        a, at, b = (
            int(join["silence_start_ms"]),
            int(join["cursor_ms"]),
            int(join["silence_end_ms"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return (a, at, b) if join.get("source") in TIMED_SOURCES and a <= at <= b and a < b else None


def _apply(entry: dict, joins: list[dict]) -> dict:
    by_ref = {j.get("after_ref"): j for j in joins if j.get("kind", PROPOSAL) == PROPOSAL}
    cuts = [dict(c) for c in entry.get("cuts") or [] if isinstance(c, dict)]
    for cut in cuts:
        join = by_ref.get(_after_ref(cut))
        timed = _timed(join) if join else None
        if timed is None:
            continue
        cut["silence_start_ms"], cut["cursor_ms"], cut["silence_end_ms"] = timed
        cut["timing_source"] = join["source"]  # type: ignore[index]
    cursors = [c.get("cursor_ms") for c in cuts]
    out = {**entry, "cuts": cuts}
    if (
        len(cursors) == len(entry.get("cursors") or [])
        and all(isinstance(c, int) for c in cursors)
        and cursors == sorted(set(cursors))
    ):
        out["cursors"] = cursors
    return out


def timed_missed_waqf_doc(doc: dict | None, timing: dict | None) -> dict | None:
    """``doc`` with every cut the cut-timing pass timed moved to its silence."""
    if not doc or not timing:
        return doc
    timed_by_uid = timing.get("by_uid") or {}
    by_uid = doc.get("by_uid") or {}
    if not isinstance(by_uid, dict) or not isinstance(timed_by_uid, dict):
        return doc
    out = copy.copy(doc)
    out["by_uid"] = {
        uid: _apply(entry, (timed_by_uid.get(uid) or {}).get("joins") or [])
        if isinstance(entry, dict) and uid in timed_by_uid
        else entry
        for uid, entry in by_uid.items()
    }
    return out
