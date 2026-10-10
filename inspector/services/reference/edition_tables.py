"""Per-edition projections of the Hafs classifier tables.

``inspector/constants.py`` holds the hand-curated Hafs table of muqattaat
opening verses, which the segment classifier consults to flag a segment that
starts on the disconnected letters.

Under another riwayah those coordinates move. Warsh renumbers 50 of the 114
surahs, so a Hafs ``(surah, ayah)`` key is not merely a different verse there —
it can be out of range. The table is therefore **projected**, never reused:
each Hafs entry goes through ``qua_domain``'s Hafs->target word projection and
comes back as the target edition's own coordinates.

Two properties make this safe:

* **Hafs is the identity.** ``muqattaat_words("hafs")`` returns the frozen
  constant verbatim, with no ``qua_domain`` call — so the published Hafs
  reciters cannot shift, and the package stays optional. A test asserts the
  derived Hafs table equals the constant.
* **Word granularity, not verse.** Warsh's ``2:1`` is an eight-word verse whose
  *first* word is the muqattaat; a verse-level table would flag its other seven
  words too. The table is keyed to the word.

Sizes: ``muqattaat_words`` has 30 entries under every edition — under Warsh /
Qalun, Hafs 42:1:1 + 42:2:1 land in ONE verse as 42:1:1 + 42:1:2.
"""

from __future__ import annotations

from constants import MUQATTAAT_VERSES
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.reference import editions
from services.storage import cache

WordRef = tuple[int, int, int]

#: The disconnected letters always open their verse, so the Hafs word table is
#: the verse table at ``word == 1``. Asserted against the projection in the
#: Shu'bah gate.
_HAFS_MUQATTAAT_WORDS: frozenset[WordRef] = frozenset(
    (surah, ayah, 1) for surah, ayah in MUQATTAAT_VERSES
)


def _fmt(ref: WordRef) -> str:
    return f"{ref[0]}:{ref[1]}:{ref[2]}"


def _parse(ref: str) -> WordRef:
    surah, ayah, word = ref.split(":")
    return int(surah), int(ayah), int(word)


def _project_words(refs: frozenset[WordRef], riwayah: str) -> frozenset[WordRef]:
    """Map Hafs word refs onto ``riwayah``'s own coordinates.

    A Hafs word can project onto several target words (a split) or share a
    target with its neighbour (a join); both are kept, so a segment starting at
    any of the target words still classifies. A word with no counterpart in the
    target edition (``target_absent``) simply drops out — there is nothing there
    to recite.
    """
    projection = editions.projection(riwayah)
    out: set[WordRef] = set()
    for ref in refs:
        projected = projection.project_range(_fmt(ref))
        for group in projected.groups:
            out.update(_parse(word.ref) for word in group.target_words)
    return frozenset(out)


def _tables(riwayah: str) -> dict:
    cached = cache.get_edition_tables(riwayah)
    if cached is not None:
        return cached
    if riwayah == DEFAULT_SDK_RIWAYAH:
        built = _hafs_tables()
    else:
        built = _projected_tables(riwayah)
    cache.set_edition_tables(riwayah, built)
    return built


def _hafs_tables() -> dict:
    """The frozen constants, verbatim — the identity case."""
    return {
        "muqattaat_words": _HAFS_MUQATTAAT_WORDS,
        "fatiha_last_ayah": 7,
    }


def _projected_tables(riwayah: str) -> dict:
    return {
        "muqattaat_words": _project_words(_HAFS_MUQATTAAT_WORDS, riwayah),
        "fatiha_last_ayah": editions.surah(1, riwayah).ayah_count,
    }


def muqattaat_words(riwayah: str) -> frozenset[WordRef]:
    """``(surah, ayah, word)`` of every disconnected-letters opening.

    Word-keyed because an edition can put two openings in one verse: Warsh
    merges Hafs's 42:1 (``ha-mim``) and 42:2 (``ayn-sin-qaf``) into a single
    verse, so ``42:1:1`` and ``42:1:2`` are both muqattaat there.
    """
    return _tables(riwayah)["muqattaat_words"]


def fatiha_last_ayah(riwayah: str) -> int:
    """Al-Fatiha's final verse number — where the Amin check looks."""
    return _tables(riwayah)["fatiha_last_ayah"]


def basmala_is_numbered(riwayah: str) -> bool:
    """True iff this edition counts the Fatiha Basmala as verse ``1:1``."""
    return editions.basmala_is_numbered(riwayah)
