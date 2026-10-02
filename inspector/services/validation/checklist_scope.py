"""Which mark-ready attestations apply to a reciter.

A checkbox asks the reviewer to confirm work on some validation categories
(``CHECKLIST_CATEGORIES``). It only applies when one of those categories ever
had an item for the reciter. Three records show that:

- the live validate result lists an item (resolved ones included);
- a segment carries the category in ``ignored_categories``;
- an effective edit-history op was launched from the category's card
  (``op_context_category``), or, for failed alignments, edited a segment
  that had no ``matched_ref``.

Low confidence counts only items under the edition's strict cutoff, the
same ones its accordion badge and the mark-ready gate count.
"""

from __future__ import annotations

from qua_shared.schemas import CHECKLIST_CATEGORIES


def _live_categories(result: dict, lc_cutoff: float) -> set[str]:
    cats = {
        cat
        for cats in CHECKLIST_CATEGORIES.values()
        for cat in cats
        if cat != "low_confidence" and result.get(cat)
    }
    if any((it.get("confidence") or 0.0) < lc_cutoff for it in result.get("low_confidence") or []):
        cats.add("low_confidence")
    return cats


def _ignored_categories(entries: list[dict]) -> set[str]:
    return {
        cat
        for entry in entries
        for seg in entry.get("segments", [])
        for cat in seg.get("ignored_categories") or []
    }


def _edited_categories(history: dict) -> set[str]:
    cats: set[str] = set()
    for batch in history.get("batches") or []:
        for op in batch.get("operations") or []:
            cat = op.get("op_context_category")
            if isinstance(cat, str):
                cats.add(cat)
            before = op.get("targets_before") or []
            if any(isinstance(s, dict) and not s.get("matched_ref") for s in before):
                cats.add("failed")
    return cats


def applicable_checklist_keys(
    result: dict, entries: list[dict], history: dict, lc_cutoff: float
) -> list[str]:
    """Checklist keys, in ``CHECKLIST_CATEGORIES`` order, whose categories had an item."""
    had = (
        _live_categories(result, lc_cutoff)
        | _ignored_categories(entries)
        | _edited_categories(history)
    )
    return [key for key, cats in CHECKLIST_CATEGORIES.items() if had.intersection(cats)]
