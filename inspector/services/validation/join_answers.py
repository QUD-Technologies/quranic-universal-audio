"""The WASL / WAQF answer at a word end, read from the live segments of one recitation.

An item's answers come only from its own occurrence: the live segments of its audio
entry overlapping the time span of its root and split pieces, so a repeated passage
never answers another rendition's words. Among those, a piece ending on the word is a
boundary: WASL when marked ``is_wasl``, else WAQF; a piece under WASL recheck answers
nothing there. Inside a piece the word takes the WASL verdict stored for it, else it
is unanswered. Answers belong to words, so splits, trims, merges and ref edits never
orphan them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

WASL = "wasl"
WAQF = "waqf"


def ref_key(ref: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ref.split(":"))


class WordAnswers:
    def __init__(
        self,
        entries: Iterable[dict],
        recheck: Iterable[str] = (),
        groups: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._recheck = set(recheck)
        self._groups = groups or {}
        self._entries = [entry.get("segments", []) for entry in entries]
        self._where = {
            seg.get("segment_uid"): (i, seg) for i, segs in enumerate(self._entries) for seg in segs
        }

    def pieces(self, uid: str | None) -> list[dict]:
        """Live segments overlapping the span of ``uid`` and its split pieces."""
        found = [
            self._where[u] for u in [uid, *self._groups.get(uid or "", [])] if u in self._where
        ]
        if not found:
            return []
        entry = found[0][0]
        t0 = min(seg.get("time_start", 0) for _, seg in found)
        t1 = max(seg.get("time_end", 0) for _, seg in found)
        return [
            seg
            for seg in self._entries[entry]
            if seg.get("time_start", 0) < t1 and seg.get("time_end", 0) > t0
        ]

    def __call__(self, word: str, pieces: Iterable[dict]) -> str | None:
        try:
            at = ref_key(word)
        except ValueError:
            return None
        for seg in pieces:
            span = _span(seg.get("matched_ref"))
            if not span or not span[0] <= at <= span[1]:
                continue
            if at == span[1]:
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
