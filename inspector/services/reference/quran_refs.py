"""Quran reference-data payload, one immutable static asset per edition.

Bundles the fixed reference data the Segments tab needs at edit time: the word
map (``dk_words``), the per-verse word counts (``verse_word_counts``), the
end-of-ayah marker prefix (``verse_marker_prefix``) and the opener specials'
Arabic text (``special_texts``, what a ``Basmala`` / ``Isti'adha`` row shows). All are constant across users, reciters, chapters and
sessions, so the frontend fetches one payload per edition per browser via
``/api/static/quran-refs.json?riwayah=…`` (immutable, content-hashed) and shares
it across every tab.

The payload is built lazily per riwayah and memoised in
``services/storage/cache.py``; the SHA-256 prefix of the serialised bytes powers
the ETag + cache-busting query param, and changes only when the underlying
script or surah metadata is rebuilt.

``verse_marker_prefix`` is U+06DD for Hafs, whose Digital Khatt font needs the
ornament sent alongside the Arabic-Indic digits, and empty for the three
packaged QPC fonts, which decorate the digits themselves — sending both would
render two nested ornaments. This mirrors the aligner app's
``src/core/quran_refs_bundle.py`` exactly, so a ref preview looks the same in
both apps.
"""

from __future__ import annotations

import hashlib
import threading

import orjson

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.storage import cache
from services.storage.data_loader import get_dk_words_flat, get_word_counts

#: U+06DD ARABIC END OF AYAH.
VERSE_MARKER = "۝"

_lock = threading.Lock()


def verse_marker_prefix(riwayah: str = DEFAULT_SDK_RIWAYAH) -> str:
    """The glyph to put before an Arabic-Indic verse number in this edition."""
    return VERSE_MARKER if riwayah == DEFAULT_SDK_RIWAYAH else ""


#: The Hafs verse words an opener is read as (qua_sdk ``special_native``): a Hafs row draws
#: from the DK word map, so an opener row shows these words like any verse.
_HAFS_OPENER_WORDS = {
    "Isti'adha": ("2:67:15", "16:98:5", "16:98:6", "16:98:7", "16:98:8"),
    "Basmala": ("1:1:1", "1:1:2", "1:1:3", "1:1:4"),
}


def special_texts(riwayah: str = DEFAULT_SDK_RIWAYAH) -> dict[str, str]:
    """The Arabic text of each opener special in this edition's script, keyed by its
    ``matched_ref`` name. Other specials (Amin, Takbir …) have no edition text."""
    if riwayah == DEFAULT_SDK_RIWAYAH:
        dk = get_dk_words_flat(riwayah)
        return {
            name: " ".join(dk[ref] for ref in refs)
            for name, refs in _HAFS_OPENER_WORDS.items()
            if all(ref in dk for ref in refs)
        }
    from services.reference import editions

    return {name: editions.special(name, riwayah) for name in _HAFS_OPENER_WORDS}


def _build(riwayah: str) -> tuple[bytes, str]:
    """Serialise the reference bundle for one edition and hash it."""
    verse_word_counts = {
        f"{surah}:{ayah}": n for (surah, ayah), n in get_word_counts(riwayah).items()
    }
    body = orjson.dumps(
        {
            "riwayah": riwayah,
            "dk_words": get_dk_words_flat(riwayah),
            "verse_word_counts": verse_word_counts,
            "verse_marker_prefix": verse_marker_prefix(riwayah),
            "special_texts": special_texts(riwayah),
        }
    )
    digest = hashlib.sha256(body).hexdigest()[:12]
    return body, digest


def _ensure(riwayah: str) -> tuple[bytes, str]:
    cached = cache.get_edition_refs_payload(riwayah)
    if cached is not None:
        return cached
    with _lock:
        cached = cache.get_edition_refs_payload(riwayah)
        if cached is None:
            cached = _build(riwayah)
            cache.set_edition_refs_payload(riwayah, cached)
    return cached


def build_payload(riwayah: str = DEFAULT_SDK_RIWAYAH) -> bytes:
    """Return the serialised Quran-refs JSON body for one edition."""
    body, _ = _ensure(riwayah)
    return body


def payload_hash(riwayah: str = DEFAULT_SDK_RIWAYAH) -> str:
    """Return the 12-char SHA-256 prefix used as ETag + cache buster."""
    _, digest = _ensure(riwayah)
    return digest


def reset_cache() -> None:
    """Drop every memoised payload. Test-only — prod has no rebuild trigger."""
    with _lock:
        cache.clear_edition_refs_payloads()


# ---------------------------------------------------------------------------
# matched_ref -> word-map text resolver
#
# Mirror of `frontend/src/tabs/segments/utils/data/references.ts::dkTextForRef`.
# Server-side derivation of the Arabic text for a canonical
# ``surah:ayah:word-surah:ayah:word`` ref. Migration #5 removed any
# per-seg / per-snapshot text field; this is the canonical source.
# ---------------------------------------------------------------------------

_MAX_AYAH_BOUNDARY = 300  # mirrors references.ts; runaway-ayah guard


def dk_text_for_ref(matched_ref: str | None, riwayah: str = DEFAULT_SDK_RIWAYAH) -> str:
    """Walk the edition's word map from the start endpoint through the end
    endpoint (inclusive). Returns ``""`` for malformed or missing input so
    callers can short-circuit on falsy.

    The ref must already be in ``riwayah``'s own coordinates — this walks by
    that edition's verse word counts, and a Hafs ref would run off the end of a
    Warsh verse (or address a verse that does not exist there at all).
    """
    if not matched_ref or "-" not in matched_ref:
        return ""
    start, _, end = matched_ref.partition("-")
    s_parts = start.split(":")
    e_parts = end.split(":")
    if len(s_parts) != 3 or len(e_parts) != 3:
        return ""
    try:
        s_su, s_ay, s_w = int(s_parts[0]), int(s_parts[1]), int(s_parts[2])
        e_su, e_ay, e_w = int(e_parts[0]), int(e_parts[1]), int(e_parts[2])
    except ValueError:
        return ""

    # Segments don't cross surahs in practice; mirror the FE assumption.
    su = s_su
    dk = get_dk_words_flat(riwayah)
    wc = get_word_counts(riwayah)

    words: list[str] = []
    ay, w = s_ay, s_w
    while (su, ay, w) <= (e_su, e_ay, e_w):
        t = dk.get(f"{su}:{ay}:{w}")
        if t:
            words.append(t)
        w += 1
        max_w = wc.get((su, ay), 0)
        if w > max_w:
            w = 1
            ay += 1
            if ay > _MAX_AYAH_BOUNDARY:
                break
    return " ".join(words)
