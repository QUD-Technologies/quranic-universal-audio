"""One Low Confidence Waqf document from the two stop signals of a fresh align run.

* ``neural`` — the aligner's review of its neural timing checks (``qua_timing_batch.retime.review``):
  a mid-verse join where the timing lattice scores a stop within its margin of reading through.
* ``lattice`` — the matcher lattice's pauses judged on the chapter's loudness levels
  (:mod:`.pause_sidecar`), which also asks verse ends the lattice and the silence disagree on.

A segment flagged by either gets one card. A join both flag is one cut carrying both axes (the
card shows them as chips) at the neural cut's cursor; the other joins keep their own cut. The
neural review's exclusions hold for the union: a mid-verse sakt boundary is never asked. When the
merged cuts do not fit the segment (cursors out of order, refs that do not tile it) the card
falls back to the neural item.
"""

from __future__ import annotations

from .pause_sidecar import _pieces

NEURAL = "neural"
LATTICE = "lattice"
#: Mid-verse sakt boundaries (``qua_domain.pauses.sakt_points``): a pause there is the sakt.
SAKT_AFTER = frozenset({"75:27:2", "83:14:2"})


def _join(cut: dict) -> tuple[str, str] | None:
    for axis in (NEURAL, LATTICE):
        ev = (cut.get("evidence") or {}).get(axis)
        if ev and ev.get("after_ref") and ev.get("next_ref"):
            return ev["after_ref"], ev["next_ref"]
    return None


def _span(item: dict) -> str | None:
    refs = item.get("refs") or []
    if not refs:
        return None
    return f"{refs[0].partition('-')[0]}-{refs[-1].partition('-')[2]}"


def _both(neural: dict, lattice: dict) -> dict:
    return {
        **neural,
        "axes": [NEURAL, LATTICE],
        "gap_ms": max(neural.get("gap_ms") or 0, lattice.get("gap_ms") or 0),
        "score": max(neural.get("score") or 0, lattice.get("score") or 0),
        "evidence": {**lattice.get("evidence", {}), **neural.get("evidence", {})},
    }


def merge_item(neural: dict | None, lattice: dict | None) -> dict | None:
    """The card for one segment from its neural and matcher-lattice items (either may be absent)."""
    lattice_cuts = [
        c for c in (lattice or {}).get("cuts") or [] if (_join(c) or ("",))[0] not in SAKT_AFTER
    ]
    if neural is None and not lattice_cuts:
        return None
    by_join: dict[tuple[str, str], dict] = {}
    for cut in lattice_cuts:
        by_join[_join(cut)] = cut
    for cut in (neural or {}).get("cuts") or []:
        key = _join(cut)
        by_join[key] = _both(cut, by_join[key]) if key in by_join else cut
    cuts = sorted(by_join.values(), key=lambda c: c["cursor_ms"])
    base = neural or lattice
    cursors = [c["cursor_ms"] for c in cuts]
    span = _span(base)
    refs = None if span is None else _pieces(span, [_join(c) for c in cuts])
    if cursors != sorted(set(cursors)) or (base.get("refs") is not None and refs is None):
        return neural
    return {**base, "cursors": cursors, "refs": refs, "score": max(c["score"] for c in cuts),
            "cuts": cuts}  # fmt: skip


def merge(neural: dict, lattice: dict | None) -> dict:
    """The union document; its ``_meta`` is the neural review's, with both arms and their counts."""
    lattice = lattice or {}
    n_items, l_items = neural.get("by_uid") or {}, lattice.get("by_uid") or {}
    by_uid = {}
    for uid in sorted(set(n_items) | set(l_items)):
        item = merge_item(n_items.get(uid), l_items.get(uid))
        if item is not None:
            by_uid[uid] = item
    axes = [set(c.get("axes") or []) for item in by_uid.values() for c in item["cuts"]]
    meta = neural.get("_meta") or {}
    arms = {**(lattice.get("_meta") or {}).get("arms", {}), **meta.get("arms", {})}
    return {
        **neural,
        "_meta": {
            **meta,
            "arms": arms,
            "segments": len(by_uid),
            "by_axes": {
                NEURAL: sum(NEURAL in a for a in axes),
                LATTICE: sum(LATTICE in a for a in axes),
                "both": sum(a >= {NEURAL, LATTICE} for a in axes),
            },
        },
        "by_uid": by_uid,
    }
