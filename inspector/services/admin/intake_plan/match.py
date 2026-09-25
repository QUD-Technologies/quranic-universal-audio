"""Playlist entry title → the chapter(s) it holds.

Real playlists are not a clean 1→114: file-index prefixes disagree with the
surah (``07 سورة الأنفال`` is surah 8), reciter names contain surah names
(``محمد``), one file may hold two surahs (``سورتا الفاتحة والبقرة``) or a juz'
(``جزء عم``), titles are Arabic, English, both, or a bare ``031.mp3``. So the
title's *name* wins over any number, and the playlist position is never trusted.

The scan is anchored on a surah keyword (``سورة`` / ``Surah``): names are read
from the tokens right after it, joined by ``و`` / ``and`` / another keyword, and
reading stops at the first token that is not a name — which is what keeps a
reciter's name out of the match. Without a keyword only unambiguous forms count
(``ال``-prefixed Arabic names, article-prefixed English ones, or a title that is
nothing but the name). ``low`` confidence marks what the owner must eyeball.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_VOCAB_FILE = Path(__file__).with_name("surah_names.json")
_MAX_NAME_TOKENS = 3
_FUZZY_MIN_RATIO = 0.86
_FUZZY_MIN_KEY_LEN = 5
_LAST_CHAPTER = 114

_AR_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_AR_FOLD = str.maketrans(
    {
        "\u0622": "\u0627",  # alef madda
        "\u0623": "\u0627",  # alef hamza above
        "\u0625": "\u0627",  # alef hamza below
        "\u0671": "\u0627",  # alef wasla
        "\u0629": "\u0647",  # taa marbuta -> haa
        "\u0649": "\u064a",  # alef maqsura -> yaa
        "\u0624": "\u0648",  # waw hamza
        "\u0626": "\u064a",  # yaa hamza
        "\u0621": "",  # standalone hamza
        **{chr(0x0660 + d): str(d) for d in range(10)},  # Arabic-Indic digits
        **{chr(0x06F0 + d): str(d) for d in range(10)},  # Extended Arabic-Indic digits
    }
)
_AR_ARTICLE = "\u0627\u0644"  # "al"
_AR_WAW = "\u0648"
_PUNCT = re.compile(r"[^\w\s]|_")
_DIGIT_EDGE = re.compile(r"(?<=\d)(?=\D)|(?<=\D)(?=\d)")
_AUDIO_EXT = re.compile(r"\.(mp3|m4a|wav|flac|ogg|opus|aac|wma|mp4|webm)$", re.IGNORECASE)
_EN_ARTICLE = re.compile(r"^(aal|al|an|ar|as|ash|at|ad|adh|az|ath)\s+")


def normalize(text: str) -> str:
    """Lowercase, fold Arabic spelling variants, strip marks + punctuation."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _AR_MARKS.sub("", t).translate(_AR_FOLD).lower()
    t = _PUNCT.sub(" ", t)
    t = _DIGIT_EDGE.sub(" ", t)
    return " ".join(t.split())


def _en_key(text: str) -> str:
    """A spelling-tolerant key for a romanised surah name."""
    t = _EN_ARTICLE.sub("", normalize(text))
    t = t.replace("oo", "u").replace("ee", "i").replace("ou", "u")
    t = re.sub(r"[^a-z]", "", t)
    t = re.sub(r"(.)\1+", r"\1", t)
    t = re.sub(r"(?<=[aeiou])h$", "", t)
    return t


@dataclass(frozen=True)
class Match:
    chapters: tuple[int, ...] = ()
    confidence: str = "none"
    juz: bool = False
    #: Read from a file number alone, with no surah name in the title.
    numeric: bool = False


@dataclass
class _Vocab:
    ar: dict[tuple[str, ...], int] = field(default_factory=dict)
    ar_bare: dict[tuple[str, ...], int] = field(default_factory=dict)
    en: dict[str, int] = field(default_factory=dict)
    juz: dict[tuple[str, ...], int] = field(default_factory=dict)
    surah_kw: frozenset[str] = frozenset()
    juz_kw: frozenset[str] = frozenset()
    range_words: frozenset[str] = frozenset()
    joiners: frozenset[str] = frozenset()
    blockers: frozenset[str] = frozenset()
    juz_first: dict[int, int] = field(default_factory=dict)


def _toks(text: str) -> tuple[str, ...]:
    return tuple(normalize(text).split())


@lru_cache(maxsize=1)
def vocab() -> _Vocab:
    from services.storage.data_loader import load_surah_info_lite

    raw = json.loads(_VOCAB_FILE.read_text(encoding="utf-8"))
    v = _Vocab(
        surah_kw=frozenset(normalize(w) for w in raw["surah_keywords"]),
        juz_kw=frozenset(normalize(w) for w in raw["juz_keywords"]),
        range_words=frozenset(normalize(w) for w in raw["range_words"]),
        joiners=frozenset(normalize(w) for w in raw["joiners"]),
        blockers=frozenset(normalize(w) for w in raw["loose_blockers"]),
        juz_first={int(k): int(c) for k, c in raw["juz_first_surah"].items()},
    )
    keyword = next(iter(_toks(raw["surah_keywords"][0])))
    for num, info in load_surah_info_lite().items():
        ch = int(num)
        tokens = tuple(t for t in _toks(info["name_ar"]) if t != keyword)
        _add_ar(v, tokens, ch)
        v.en.setdefault(_en_key(info["name_en"]), ch)
    for num, names in raw["ar_aliases"].items():
        for name in names:
            _add_ar(v, _toks(name), int(num))
    for num, names in raw["en_aliases"].items():
        for name in names:
            v.en.setdefault(_en_key(name), int(num))
    for num, names in raw["juz_aliases"].items():
        for name in names:
            v.juz[_toks(name)] = int(num)
            if _en_key(name):
                v.juz[(_en_key(name),)] = int(num)
    return v


def _add_ar(v: _Vocab, tokens: tuple[str, ...], ch: int) -> None:
    if not tokens:
        return
    v.ar.setdefault(tokens, ch)
    head = tokens[0]
    if head.startswith(_AR_ARTICLE) and len(head) > len(_AR_ARTICLE) + 1:
        v.ar_bare.setdefault((head[len(_AR_ARTICLE) :], *tokens[1:]), ch)


# ---------------------------------------------------------------------------
# Name lookup at one token position
# ---------------------------------------------------------------------------


def _name_at(toks: tuple[str, ...], i: int, *, bare: bool) -> tuple[int, int] | None:
    """``(chapter, tokens consumed)`` for the name starting at ``i``.

    Exact forms are tried longest-first; the fuzzy English fallback shortest-
    first, so a trailing word (``An-Nas Dr``) is never absorbed into a near
    miss of a longer name (``An-Nasr``)."""
    v = vocab()
    widths = range(min(_MAX_NAME_TOKENS, len(toks) - i), 0, -1)
    for n in widths:
        window = toks[i : i + n]
        for table in (v.ar, v.ar_bare) if bare else (v.ar,):
            if window in table:
                return table[window], n
        ch = _en_lookup(" ".join(window), fuzzy=False)
        if ch is not None:
            return ch, n
    if bare:
        for n in reversed(widths):
            ch = _en_lookup(" ".join(toks[i : i + n]), fuzzy=True)
            if ch is not None:
                return ch, n
    head = toks[i] if i < len(toks) else ""
    if head.startswith(_AR_WAW) and len(head) > 2:  # "wa-" glued to the next name
        hit = _name_at((head[1:], *toks[i + 1 :]), 0, bare=bare)
        if hit is not None:
            return hit
    return None


def _en_lookup(text: str, *, fuzzy: bool) -> int | None:
    key = _en_key(text)
    if not key:
        return None
    table = vocab().en
    if key in table:
        return table[key]
    if not fuzzy or len(key) < _FUZZY_MIN_KEY_LEN:
        return None
    best = difflib.get_close_matches(key, table.keys(), n=2, cutoff=_FUZZY_MIN_RATIO)
    if len(best) == 1 or (len(best) == 2 and table[best[0]] == table[best[1]]):
        return table[best[0]]
    return None


# ---------------------------------------------------------------------------
# Title → Match
# ---------------------------------------------------------------------------


def _read_names(toks: tuple[str, ...], i: int) -> tuple[list[int], bool]:
    """Names from ``i`` joined by connectors. Returns ``(chapters, is_range)``."""
    v = vocab()
    found: list[int] = []
    is_range = False
    while i < len(toks):
        hit = _name_at(toks, i, bare=True)
        if hit is None:
            break
        found.append(hit[0])
        i += hit[1]
        consumed = False
        while i < len(toks) and (toks[i] in v.joiners or toks[i] in v.range_words):
            is_range = is_range or toks[i] in v.range_words
            i += 1
            consumed = True
        # "الفاتحة وسورة البقرة": a repeated keyword (bare or waw-prefixed) joins too.
        if i < len(toks) and _is_keyword(toks[i]):
            i += 1
            consumed = True
        if not consumed and not (i < len(toks) and toks[i].startswith(_AR_WAW)):
            break
    return found, is_range


def _is_keyword(tok: str) -> bool:
    kw = vocab().surah_kw
    return tok in kw or (tok.startswith(_AR_WAW) and tok[1:] in kw)


def _chapters_from(found: list[int], is_range: bool) -> tuple[int, ...]:
    if len(found) >= 2 and is_range and found[-1] > found[0]:
        return tuple(range(found[0], found[-1] + 1))
    return tuple(sorted(set(found)))


def _leading_number(toks: tuple[str, ...]) -> int | None:
    for t in toks:
        if t.isdigit():
            n = int(t)
            if 1 <= n <= _LAST_CHAPTER:
                return n
    return None


def _juz_match(toks: tuple[str, ...]) -> Match | None:
    v = vocab()
    for i, t in enumerate(toks):
        if t not in v.juz_kw:
            continue
        rest = toks[i + 1 :]
        if rest and rest[0].isdigit() and int(rest[0]) in v.juz_first:
            return Match((v.juz_first[int(rest[0])],), "low", juz=True)
        first = _juz_alias(rest)
        if first is None and rest:
            hit = _name_at(rest, 0, bare=True)
            first = hit[0] if hit is not None else None
        if first is not None:
            return Match((first,), "low", juz=True)
    return None


def _juz_alias(rest: tuple[str, ...]) -> int | None:
    juz = vocab().juz
    for n in range(min(_MAX_NAME_TOKENS, len(rest)), 0, -1):
        if rest[:n] in juz:
            return juz[rest[:n]]
        key = _en_key(" ".join(rest[:n]))
        if key and (key,) in juz:
            return juz[(key,)]
        near = difflib.get_close_matches(key, [k[0] for k in juz if len(k) == 1], n=1, cutoff=0.8)
        if key and len(key) >= _FUZZY_MIN_KEY_LEN and near:
            return juz[(near[0],)]
    return None


def match_title(title: str) -> Match:
    stem = _AUDIO_EXT.sub("", (title or "").strip())
    toks = _toks(stem)
    if not toks:
        return Match()
    v = vocab()
    number = _leading_number(toks)
    candidates: list[tuple[int, ...]] = []
    for i, t in enumerate(toks):
        if t in v.surah_kw:
            found, is_range = _read_names(toks, i + 1)
            if found:
                candidates.append(_chapters_from(found, is_range))
    if candidates:
        first: tuple[int, ...] = candidates[0]
        agree = all(c == first for c in candidates)
        if not agree:
            return Match(first, "low")
        if len(first) > 1:
            return Match(first, "high")
        exact = number is None or (number,) == first
        return Match(first, "exact" if exact else "high")
    juz = _juz_match(toks)
    if juz is not None:
        return juz
    loose, confident = _loose_match(toks)
    if loose:
        return Match(loose, "high" if confident else "low")
    if number is not None:
        numeric_only = all(t.isdigit() for t in toks)
        return Match((number,), "high" if numeric_only else "low", numeric=True)
    return Match()


def _loose_match(toks: tuple[str, ...]) -> tuple[tuple[int, ...], bool]:
    """No keyword: only forms that cannot be a reciter's name count — an
    ``ال``-prefixed Arabic name, an exact romanised name, an article-prefixed
    near miss, or a title that is nothing but a name. Adjacent names (``01
    Al-Fatihah Al-Baqarah``) are one combined file.

    Returns ``(chapters, confident)``: confident when the title is nothing but
    the name or the name directly follows a file number (``22 Al-Muminun``)."""
    words = tuple(t for t in toks if not t.isdigit())
    whole = _name_at(words, 0, bare=True) if words else None
    if whole is not None and whole[1] == len(words):
        return (whole[0],), True
    blockers = vocab().blockers
    runs: list[tuple[list[int], bool]] = []
    last_end = -1
    i = 0
    while i < len(toks):
        hit = None if i and toks[i - 1] in blockers else _loose_name_at(toks, i)
        if hit is None:
            i += 1
            continue
        if runs and i == last_end:
            runs[-1][0].append(hit[0])
        else:
            runs.append(([hit[0]], i > 0 and toks[i - 1].isdigit()))
        i += hit[1]
        last_end = i
    if not runs:
        return (), False
    chapters, after_number = runs[-1]
    return tuple(sorted(set(chapters))), after_number


def _loose_name_at(toks: tuple[str, ...], i: int) -> tuple[int, int] | None:
    t = toks[i]
    if t.isdigit():
        return None
    if t.startswith(_AR_ARTICLE):
        return _name_at(toks, i, bare=False)
    for n in range(min(_MAX_NAME_TOKENS, len(toks) - i), 0, -1):
        window = " ".join(toks[i : i + n])
        ch = _en_lookup(window, fuzzy=n > 1 and bool(_EN_ARTICLE.match(f"{t} ")))
        if ch is not None and len(_en_key(window)) >= 3:
            return ch, n
    return None


# ---------------------------------------------------------------------------
# A whole listing
# ---------------------------------------------------------------------------


def match_entries(titles: list[str]) -> list[Match]:
    """Match every title, then resolve juz' spans and flag duplicate claims.

    A juz' entry is named by its first surah and runs up to the surah before the
    next entry's first chapter (or to 114 when nothing follows)."""
    matches = [match_title(t) for t in titles]
    starts = sorted({m.chapters[0] for m in matches if m.chapters})
    out: list[Match] = []
    for m in matches:
        if m.juz and m.chapters:
            first = m.chapters[0]
            later = [s for s in starts if s > first]
            last = (later[0] - 1) if later else _LAST_CHAPTER
            m = Match(tuple(range(first, last + 1)), "low", juz=True)
        out.append(m)
    # A named claim beats a bare file number ("01 - Introduction" is not 1).
    named = {c for m in out if not m.numeric for c in m.chapters}
    out = [Match() if m.numeric and set(m.chapters) & named else m for m in out]
    claims: dict[int, int] = {}
    for m in out:
        for ch in m.chapters:
            claims[ch] = claims.get(ch, 0) + 1
    return [
        Match(m.chapters, "low", m.juz, m.numeric) if any(claims[c] > 1 for c in m.chapters) else m
        for m in out
    ]
