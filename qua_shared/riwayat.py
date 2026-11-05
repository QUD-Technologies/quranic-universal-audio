"""Which riwayat Universal Audio supports, and how their slugs map to the SDK.

Single source of truth for the riwayah vocabulary boundary. Two slug spaces
exist and they do NOT agree:

- **Inspector slugs** — the ``riwayahs.slug`` column and every
  ``catalog.deliveries[].riwayah`` value (``qalon_an_nafi``, ``shubah_an_asim``).
  Twenty rows exist in the vocabulary; only the four below are supported.
- **SDK slugs** — what ``qua_domain`` / ``qua_sdk`` speak (``qalun``, ``shuba``).

This module is deliberately an explicit table rather than a call to
``qua_domain.normalize_riwayah``, for two reasons:

1. ``normalize_riwayah``'s alias table is upstream's to change. The pinned
   revision happens to accept all four Inspector spellings, but the mapping
   this repo's catalog rows depend on has to be pinned where those rows live,
   not inherited from a dependency that can drop an alias in any revision.
2. ``qua_domain`` is an optional dependency (see
   ``services/reference/editions.py``). Slug support must be answerable on a
   Hafs-only deployment where the package is not installed.

A parity test asserts this table agrees with ``normalize_riwayah`` for all four
entries whenever ``qua_domain`` IS importable, so the two can never silently
diverge.

Re-exported to the frontend as ``RiwayatConfig`` through
``qua_shared/schemas/fe_types.py`` — FE code compares against the generated
constant, never a bare ``short !== 'hafs'``.
"""

from __future__ import annotations

#: Inspector vocabulary slug -> SDK slug, for every riwayah Universal Audio
#: can align, review, time and publish. Insertion order is product order
#: (the order the aligner app offers them in).
SUPPORTED_RIWAYAT: dict[str, str] = {
    "hafs_an_asim": "hafs",
    "warsh_an_nafi": "warsh",
    "qalon_an_nafi": "qalun",
    "shubah_an_asim": "shuba",
}

#: The reverse map. Built here so callers never invert it themselves.
_SDK_TO_INSPECTOR: dict[str, str] = {v: k for k, v in SUPPORTED_RIWAYAT.items()}

DEFAULT_RIWAYAH = "hafs_an_asim"
DEFAULT_SDK_RIWAYAH = "hafs"


class UnsupportedRiwayah(ValueError):
    """A riwayah outside :data:`SUPPORTED_RIWAYAT` reached a path that needs one.

    Raised rather than defaulted: silently treating an unsupported riwayah as
    Hafs would render one edition's coordinates under another edition's script.
    """


def to_sdk_slug(inspector_slug: str | None) -> str:
    """Map an Inspector vocabulary slug to its SDK slug.

    Raises :class:`UnsupportedRiwayah` for anything outside the supported four,
    including ``None`` — callers that want a default must pass
    :data:`DEFAULT_RIWAYAH` explicitly.
    """
    try:
        return SUPPORTED_RIWAYAT[inspector_slug]  # type: ignore[index]
    except KeyError:
        raise UnsupportedRiwayah(f"unsupported riwayah: {inspector_slug!r}") from None


def from_sdk_slug(sdk_slug: str | None) -> str:
    """Map an SDK slug back to its Inspector vocabulary slug."""
    try:
        return _SDK_TO_INSPECTOR[sdk_slug]  # type: ignore[index]
    except KeyError:
        raise UnsupportedRiwayah(f"unsupported SDK riwayah: {sdk_slug!r}") from None


def resolve_sdk_slug(value: str | None) -> str:
    """Map EITHER vocabulary's slug to the SDK slug.

    The manifest's ``riwayah`` field is a plain string carrying whatever the
    catalog row holds, and fixtures/older rows can hold the short SDK form. A
    reader that must not care which vocabulary it was handed uses this; a
    *writer*, and any HTTP boundary, should stay strict with
    :func:`to_sdk_slug` so the vocabularies do not quietly interleave.
    """
    if value in _SDK_TO_INSPECTOR:
        return value  # type: ignore[return-value]
    return to_sdk_slug(value)


def is_supported(inspector_slug: str | None) -> bool:
    """True iff ``inspector_slug`` is one of the four supported riwayat."""
    return inspector_slug in SUPPORTED_RIWAYAT


def is_hafs(inspector_slug: str | None) -> bool:
    """True iff ``inspector_slug`` is Hafs.

    The Hafs branch is the one that keeps the Digital Khatt (talqeen-quran-data)
    display path and needs no ``qua_domain`` at all, so this
    predicate gates far more than a cosmetic difference.
    """
    return inspector_slug == DEFAULT_RIWAYAH
