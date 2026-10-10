"""The Inspector's only door onto ``qua_domain``.

`qua_domain` owns edition identity: the exact target script for each riwayah,
its coordinate index, counting profile, paired font, stop-sign profile, special
texts, and the Hafs->target word projection. Nothing else in this tree imports
it — every consumer goes through here, so the optional-dependency handling and
the caching live in one place.

## Hafs is deliberately not routed here

`qua_domain`'s Hafs index carries the same 77,433 word refs as the Inspector's
`data/qpc_hafs.json`, but a *different* QPC glyph variant: 44,481 of those words
spell differently (U+0652 vs U+06E1, tatweel dagger-alif vs combined). Swapping
the Hafs display path onto it would silently change every rendered segment
and every `qalqala_letter` derivation across the 37 published reciters.

So Hafs keeps `qpc_hafs.json` + DigitalKhatt, exactly as the aligner app does,
and only `warsh`/`qalun`/`shuba` take text and font from here.
:func:`word_map` refuses Hafs outright rather than quietly returning the other
spelling.

## Absent package

`qua_domain` is installed from a private monorepo pin, so it may be missing:
a fork build, a contributor without the deploy key, `INSPECTOR_MULTI_RIWAYAH=0`.
Then :func:`available` is False and every non-Hafs path raises
:class:`EditionsUnavailable` — never a silent Hafs fallback, which would render
one edition's coordinates under another's script and let a reviewer save wrong
refs.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from config import MULTI_RIWAYAH_ENABLED
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.storage import cache

log = logging.getLogger("inspector")


class EditionsUnavailable(RuntimeError):
    """A non-Hafs edition was requested but ``qua_domain`` is not usable."""


class RefNotInEdition(ValueError):
    """A ``matched_ref`` that does not name a word of the delivery's edition.

    ``reverse_range`` raises rather than returning an empty span, and it raises
    two unrelated types: ``InvalidQuranReferenceError`` for a malformed ref and
    a bare ``KeyError`` for a well-formed ref whose coordinates the edition does
    not have (Warsh and Qalun renumber 50 surahs, so a Hafs ref can be perfectly
    valid and still name nothing here). Both mean the same thing to a caller and
    both are the client's ref, so they arrive as one 400 rather than a 500.
    """


class HafsNotRoutedHere(RuntimeError):
    """Hafs text was requested from the edition accessor.

    Hafs display text is owned by ``qpc_hafs.json`` + DigitalKhatt (see the
    module docstring). A caller that reaches here for Hafs has a branch missing.
    """


@lru_cache(maxsize=1)
def _module() -> Any | None:
    """The imported ``qua_domain``, or ``None`` when unusable."""
    if not MULTI_RIWAYAH_ENABLED:
        log.info("editions: INSPECTOR_MULTI_RIWAYAH=0 — Hafs-only runtime")
        return None
    try:
        import qua_domain
    except ImportError:
        log.warning(
            "editions: qua_domain is not installed — Hafs-only runtime. "
            "Non-Hafs deliveries will fail loudly rather than render as Hafs."
        )
        return None
    return qua_domain


def available() -> bool:
    """True iff non-Hafs editions can be served."""
    return _module() is not None


def all_riwayat() -> list[str]:
    """Every SDK riwayah slug this runtime can serve; ``[]`` when unavailable."""
    module = _module()
    return list(module.SUPPORTED_RIWAYAT) if module else []


def _require(riwayah: str) -> Any:
    module = _module()
    if module is None:
        raise EditionsUnavailable(f"riwayah {riwayah!r} needs qua_domain, which is not available")
    return module


def _require_non_hafs(riwayah: str) -> Any:
    if riwayah == DEFAULT_SDK_RIWAYAH:
        raise HafsNotRoutedHere(
            "Hafs text comes from qpc_hafs.json + DigitalKhatt, not qua_domain "
            "(their glyph variants differ in 44,481 words)"
        )
    return _require(riwayah)


# ---------------------------------------------------------------------------
# Metadata + assets
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def metadata(riwayah: str) -> Any:
    """``EditionMetadata`` — ids, digests, counting profile, font descriptor."""
    return _require(riwayah).get_edition(riwayah)


@lru_cache(maxsize=8)
def stop_signs(riwayah: str) -> frozenset[str]:
    """The stop-sign glyphs actually observed in this edition's script.

    Warsh/Qalun carry only U+06D6 (as ``optional_stop``, 9,948 occurrences);
    Hafs and Shu'bah carry the six-sign inventory. Replaces the module-level
    ``constants.STOP_SIGNS`` for non-Hafs.

    Hafs stays on ``constants.STOP_SIGNS``, which is the narrower four-sign set
    — it deliberately omits U+06DB (paired stop, one of a pair must be honoured)
    and U+06DC (sakt, not a stop at all). Routing Hafs here would widen the set
    under 37 published reciters, so the Hafs caller does not.
    """
    profile = _require(riwayah).get_stop_sign_profile(riwayah)
    return frozenset(sign.glyph for sign in profile.signs)


@lru_cache(maxsize=32)
def special(name: str, riwayah: str) -> str:
    """Exact ``Basmala`` / ``Isti'adha`` text in this edition's script."""
    return _require(riwayah).special_text(name, riwayah)


def font(riwayah: str) -> tuple[bytes, Any]:
    """``(bytes, EditionAsset)`` for the edition's paired font.

    Cached by slug because each font is ~0.9 MB and the route serves it with an
    immutable digest ETag; the packaged loader verifies the digest before
    returning, so a cached entry is already proven.
    """
    cached = cache.get_edition_font(riwayah)
    if cached is not None:
        return cached
    module = _require_non_hafs(riwayah)
    asset = module.font_asset(riwayah)
    payload = (module.read_font_asset(riwayah), asset)
    cache.set_edition_font(riwayah, payload)
    return payload


# ---------------------------------------------------------------------------
# Coordinates + text
# ---------------------------------------------------------------------------


def word_map(riwayah: str) -> dict[str, str]:
    """``{"<s>:<a>:<w>": exact target text}`` for a NON-Hafs edition."""
    cached = cache.get_edition_word_map(riwayah)
    if cached is not None:
        return cached
    module = _require_non_hafs(riwayah)
    built = {word.ref: word.text for word in module.load_edition_index(riwayah).words}
    cache.set_edition_word_map(riwayah, built)
    return built


def word_counts(riwayah: str) -> dict[tuple[int, int], int]:
    """``{(surah, ayah): word_count}`` under this edition's counting profile.

    Warsh/Qalun renumber (6,214 ayahs vs Hafs's 6,236), so this is not a
    reindex of the Hafs map — it is the edition's own verse structure.
    """
    cached = cache.get_edition_word_counts(riwayah)
    if cached is not None:
        return cached
    counts: dict[tuple[int, int], int] = {}
    for ref in word_map(riwayah):
        surah, ayah, _ = ref.split(":")
        key = (int(surah), int(ayah))
        counts[key] = counts.get(key, 0) + 1
    cache.set_edition_word_counts(riwayah, counts)
    return counts


def surah(number: int, riwayah: str) -> Any:
    """``SurahMetadata`` under this edition's counting profile."""
    return _require(riwayah).get_surah(number, riwayah)


def projection(riwayah: str) -> Any:
    """The Hafs->``riwayah`` word projection (identity when ``riwayah`` is Hafs)."""
    cached = cache.get_edition_projection(riwayah)
    if cached is not None:
        return cached
    built = _require(riwayah).load_edition_projection(
        riwayah, reference_riwayah=DEFAULT_SDK_RIWAYAH
    )
    cache.set_edition_projection(riwayah, built)
    return built


def basmala_is_numbered(riwayah: str) -> bool:
    """True iff this edition counts the Fatiha Basmala as verse ``1:1``.

    Hafs and Shu'bah do; Warsh and Qalun render it as an unnumbered opener. The
    ``basmala_amin`` validation category emits its sounded-Basmala check only
    when this holds — elsewhere the offline pipeline strips the opener and there
    is no ``1:1`` to flag.
    """
    if riwayah == DEFAULT_SDK_RIWAYAH:
        return True
    return _require(riwayah).counts_basmala_as_verse(riwayah)


def provenance() -> dict[str, str]:
    """Installed-package provenance for ``/healthz`` and the boot assertion."""
    module = _module()
    if module is None:
        return {}
    info = module.projection_asset_info()
    return {
        "projection_id": module.PROJECTION_ID,
        "projection_sha256": info.sha256,
        "reference_id": module.REFERENCE_ID,
    }


def clear_caches() -> None:
    """Drop every edition-derived cache — this module's and ``cache``'s.

    ``cache.clear_edition_caches()`` on its own leaves this module's
    ``lru_cache`` layer warm, so a test that swaps ``qua_domain`` for a fake would still be
    served the real module, its metadata and its stop signs. Clear both here so
    a caller cannot get half of it.
    """
    for cached in (_module, metadata, stop_signs, special):
        # A test may have swapped one of these for a plain stub, which has no
        # ``cache_clear``; clearing the rest still has to happen.
        clear = getattr(cached, "cache_clear", None)
        if clear is not None:
            clear()
    cache.clear_edition_caches()
