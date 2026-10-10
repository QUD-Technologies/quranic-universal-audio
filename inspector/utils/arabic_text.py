"""Arabic text utilities: stripping Quranic decoration, letter extraction."""

import unicodedata as _ud
from functools import lru_cache

# Characters to strip from Quranic text for bare-skeleton comparison
_STRIP_CHARS = set("\u0640\u06de\u06e6\u06e9\u200f")


@lru_cache(maxsize=32768)
def strip_quran_deco(text: str) -> str:
    """Strip Quranic decoration and diacritics for bare-skeleton comparison.

    Memoized: Quranic text strings repeat heavily across segments (most match
    a small set of canonical verse texts), and the NFD + per-char category
    scan is hot under ``validate_reciter_segments`` (called from
    ``last_arabic_letter``).
    """
    text = _ud.normalize("NFD", text)
    out = []
    for ch in text:
        if ch in _STRIP_CHARS:
            continue
        if _ud.category(ch) == "Mn":
            continue
        out.append(ch)
    return "".join(out).strip()


@lru_cache(maxsize=32768)
def last_arabic_letter(text: str) -> str | None:
    """Return the last Arabic letter in text, ignoring diacritics and all non-letter markers.

    Scans backward after stripping diacritics/decoration so that waqf markers,
    sajdah signs, hizb markers, end-of-ayah (U+06DD), spaces, and any other
    non-letter symbols are never mistakenly returned as the last letter.

    Memoized: each segment's ``matched_text`` is one of a small set of
    canonical verse-range strings. With ~10-13k segs per reciter and a few
    thousand unique inputs, the cache delivers a high hit rate; this was
    measured at ~1.8s self-time in ``_build_detail_lists`` and collapses to
    a dict-lookup once warm.
    """
    stripped = strip_quran_deco(text)
    for ch in reversed(stripped):
        if _ud.category(ch).startswith("L"):
            return ch
    return None
