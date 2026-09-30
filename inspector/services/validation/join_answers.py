"""The WASL / WAQF answer at a word end, read from the live segments holding the word.

A piece ending on the word is a boundary: WASL when marked ``is_wasl``, else WAQF;
a piece under WASL recheck answers nothing there. Inside a piece the word takes the
WASL verdict stored for it, else it is unanswered. Answers belong to words, so
splits, trims, merges and ref edits never orphan them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

WASL = "wasl"
WAQF = "waqf"


def ref_key(ref: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ref.split(":"))


class WordAnswers:
    def __init__(self, entries: Iterable[dict], recheck: Iterable[str] = ()) -> None:
        self._recheck = set(recheck)
        self._by_surah: dict[int, list[tuple[tuple[int, ...], tuple[int, ...], dict]]] = (
            defaultdict(list)
        )
        for entry in entries:
            for seg in entry.get("segments", []):
                span = _span(seg.get("matched_ref"))
                if span:
                    self._by_surah[span[0][0]].append((*span, seg))

    def __call__(self, word: str) -> str | None:
        try:
            at = ref_key(word)
        except ValueError:
            return None
        for start, end, seg in self._by_surah.get(at[0], ()):
            if not start <= at <= end:
                continue
            if at == end:
                if seg.get("segment_uid") in self._recheck:
                    return None
                return WASL if seg.get("is_wasl") is True else WAQF
            stored = [j for j in seg.get("join_verdicts") or [] if j.get("after_ref") == word]
            if any(j.get("verdict") == WASL for j in stored):
                return WASL
        return None


def _span(ref: object) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    if not isinstance(ref, str) or "-" not in ref:
        return None
    start, _, end = ref.partition("-")
    try:
        a, b = ref_key(start), ref_key(end)
    except ValueError:
        return None
    return (a, b) if len(a) == 3 and len(b) == 3 and a <= b else None
