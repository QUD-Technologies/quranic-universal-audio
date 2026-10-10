"""The projected classifier tables.

The load-bearing gate is the FIRST test: Shu'bah is coordinate-identical to Hafs
after its three script overlays, so projecting the Hafs tables into Shu'bah must
reproduce them exactly. That is what proves the projection machinery itself is
sound — the Hafs case returns the constants without projecting, so it cannot
catch a broken projection on its own.
"""

from __future__ import annotations

import pytest

from constants import MUQATTAAT_VERSES
from services.reference import edition_tables, editions

TABLES = ("muqattaat_words",)
has_editions = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)


@pytest.fixture(autouse=True)
def _clear_edition_caches():
    yield
    editions.clear_caches()


# ---------------------------------------------------------------------------
# Hafs — no projection, and no qua_domain
# ---------------------------------------------------------------------------


def test_hafs_tables_are_the_frozen_constants():
    assert edition_tables.muqattaat_words("hafs") == {(s, a, 1) for s, a in MUQATTAAT_VERSES}
    assert edition_tables.fatiha_last_ayah("hafs") == 7
    assert edition_tables.basmala_is_numbered("hafs") is True


def test_hafs_tables_build_without_the_optional_package(monkeypatch):
    # 37 published Hafs reciters must classify identically on a runtime that
    # never had the deploy key.
    monkeypatch.setattr(editions, "_module", lambda: None)
    for table in TABLES:
        assert getattr(edition_tables, table)("hafs")


# ---------------------------------------------------------------------------
# The projection gate
# ---------------------------------------------------------------------------


@has_editions
@pytest.mark.parametrize("table", TABLES)
def test_projecting_into_shuba_reproduces_the_hafs_table(table):
    # Shu'bah shares Hafs's coordinates and counting profile, so this compares a
    # fully projected table against the hand-curated constant. A projection bug
    # (wrong direction, dropped split, off-by-one word) fails here.
    assert getattr(edition_tables, table)("shuba") == getattr(edition_tables, table)("hafs")


@has_editions
def test_shuba_tables_really_go_through_the_projection():
    # Guards the gate above against becoming a tautology if `shuba` were ever
    # short-circuited to the Hafs constants the way `hafs` is.
    calls: list[str] = []
    real = editions.projection

    def spy(riwayah):
        calls.append(riwayah)
        return real(riwayah)

    original = edition_tables.editions.projection
    edition_tables.editions.projection = spy
    try:
        edition_tables.muqattaat_words("shuba")
    finally:
        edition_tables.editions.projection = original
    assert calls and set(calls) == {"shuba"}


# ---------------------------------------------------------------------------
# Warsh / Qalun — where the coordinates actually move
# ---------------------------------------------------------------------------


@has_editions
@pytest.mark.parametrize("riwayah", ("warsh", "qalun"))
def test_the_two_shura_openings_share_one_verse_but_stay_two_words(riwayah):
    # Hafs 42:1 and 42:2 are two one-word verses; Warsh merges them into a
    # single verse of two words. The word table keeps both openings.
    words = edition_tables.muqattaat_words(riwayah)
    assert len(words) == len(MUQATTAAT_VERSES)
    assert (42, 1, 1) in words
    assert (42, 1, 2) in words
    assert (42, 2, 1) not in words


@has_editions
@pytest.mark.parametrize("riwayah", ("warsh", "qalun"))
def test_the_amin_check_still_looks_at_fatihas_seventh_verse(riwayah):
    # The Basmala is unnumbered here, but Warsh splits a later verse to
    # compensate, so al-Fatiha still ends at 7.
    assert edition_tables.fatiha_last_ayah(riwayah) == 7
    assert edition_tables.basmala_is_numbered(riwayah) is False


@has_editions
def test_tables_are_memoised_per_edition():
    first = edition_tables.muqattaat_words("warsh")
    assert edition_tables.muqattaat_words("warsh") is first
    assert edition_tables.muqattaat_words("qalun") is not first
