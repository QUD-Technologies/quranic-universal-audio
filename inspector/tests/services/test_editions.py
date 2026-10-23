"""The ``qua_domain`` accessor — the Inspector's only door onto edition data.

Two halves. The first runs everywhere and pins the DEGRADED contract: without
the package, non-Hafs raises and nothing silently falls back to Hafs. The second
needs the package installed (CI installs it from the deploy key; a fork skips)
and pins what the real edition data must satisfy for the Segments/Timestamps
surfaces to be correct.
"""

from __future__ import annotations

import pytest

from services.reference import editions

pytestmark = pytest.mark.usefixtures("_clear_edition_caches")

NON_HAFS = ("warsh", "qalun", "shuba")
has_editions = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)


@pytest.fixture
def _clear_edition_caches():
    yield
    editions.clear_caches()


@pytest.fixture
def absent(monkeypatch):
    """Simulate a runtime built without the deploy key."""
    monkeypatch.setattr(editions, "_module", lambda: None)


# ---------------------------------------------------------------------------
# Degraded runtime
# ---------------------------------------------------------------------------


def test_reports_unavailable_and_serves_no_riwayat_without_the_package(absent):
    assert editions.available() is False
    assert editions.all_riwayat() == []
    assert editions.provenance() == {}


@pytest.mark.parametrize("riwayah", NON_HAFS)
def test_every_edition_accessor_raises_rather_than_falling_back_to_hafs(absent, riwayah):
    # A silent Hafs fallback would render one edition's coordinates under
    # another's script, and a reviewer would save wrong refs against it.
    for call in (
        lambda: editions.metadata(riwayah),
        lambda: editions.word_map(riwayah),
        lambda: editions.word_counts(riwayah),
        lambda: editions.font(riwayah),
        lambda: editions.projection(riwayah),
        lambda: editions.stop_signs(riwayah),
        lambda: editions.surah(1, riwayah),
    ):
        with pytest.raises(editions.EditionsUnavailable):
            call()


def test_disabling_the_flag_makes_the_runtime_hafs_only(monkeypatch):
    monkeypatch.setattr(editions, "MULTI_RIWAYAH_ENABLED", False)
    editions._module.cache_clear()
    try:
        assert editions.available() is False
    finally:
        editions._module.cache_clear()


# ---------------------------------------------------------------------------
# Installed runtime
# ---------------------------------------------------------------------------


@has_editions
def test_serves_exactly_the_four_supported_riwayat():
    assert editions.all_riwayat() == ["hafs", "warsh", "qalun", "shuba"]


@has_editions
def test_hafs_text_is_refused_so_the_qpc_glyph_variant_stays_authoritative():
    # qua_domain's Hafs index carries the same 77,433 refs but a different QPC
    # glyph variant (44,481 words differ). Serving it would change every
    # rendered segment under the 37 published reciters.
    with pytest.raises(editions.HafsNotRoutedHere):
        editions.word_map("hafs")
    with pytest.raises(editions.HafsNotRoutedHere):
        editions.font("hafs")


@has_editions
@pytest.mark.parametrize(
    ("riwayah", "words", "ayahs"),
    [("warsh", 77428, 6214), ("qalun", 77428, 6214), ("shuba", 77433, 6236)],
)
def test_coordinate_totals_match_the_editions_own_counting_profile(riwayah, words, ayahs):
    word_map = editions.word_map(riwayah)
    assert len(word_map) == words
    assert len(editions.word_counts(riwayah)) == ayahs
    assert sum(editions.word_counts(riwayah).values()) == words


@has_editions
@pytest.mark.parametrize("riwayah", NON_HAFS)
def test_word_counts_agree_with_the_packages_own_per_ayah_lookup(riwayah):
    # word_counts() derives from the word map; qua_domain has an independent
    # ayah-shape table. A disagreement means the two coordinate sources drifted.
    import qua_domain

    counts = editions.word_counts(riwayah)
    for surah in (1, 2, 57, 114):
        last = editions.surah(surah, riwayah).ayah_count
        for ayah in (1, last):
            assert counts[(surah, ayah)] == qua_domain.get_ayah_word_count(surah, ayah, riwayah)


@has_editions
def test_warsh_renumbers_fifty_surahs_so_a_hafs_verse_ref_can_be_out_of_range():
    # This is why every displayed coordinate has to come from the delivery's own
    # edition: 2:286 exists in Hafs and Shu'bah but NOT in Warsh/Qalun, whose
    # al-Baqarah ends at 285. A Hafs-keyed verse timeline would render a
    # phantom last verse and drop one word's worth of audio off the end.
    renumbered = [
        surah
        for surah in range(1, 115)
        if editions.surah(surah, "warsh").ayah_count != editions.surah(surah, "hafs").ayah_count
    ]
    assert len(renumbered) == 50
    assert (2, 286) in editions.word_counts("shuba")
    assert (2, 286) not in editions.word_counts("warsh")
    assert (2, 285) in editions.word_counts("warsh")


@has_editions
def test_warsh_carries_one_stop_sign_and_shuba_the_full_inventory():
    assert editions.stop_signs("warsh") == frozenset("ۖ")
    assert editions.stop_signs("shuba") == frozenset("ۖۗۘۚۛۜ")


@has_editions
@pytest.mark.parametrize("riwayah", NON_HAFS)
def test_font_bytes_match_the_declared_digest_and_size(riwayah):
    import hashlib

    payload, asset = editions.font(riwayah)
    assert len(payload) == asset.size
    assert hashlib.sha256(payload).hexdigest() == asset.sha256
    assert asset.media_type.startswith("font/")


@has_editions
@pytest.mark.parametrize("riwayah", NON_HAFS)
def test_font_is_served_from_cache_on_the_second_read(riwayah):
    first, _ = editions.font(riwayah)
    second, _ = editions.font(riwayah)
    # Identity, not equality: a re-read would decompress ~0.9 MB per request.
    assert first is second


@has_editions
def test_opening_basmala_projects_to_no_verse_where_it_is_not_one():
    warsh = editions.projection("warsh")
    opener = warsh.project_range("1:1:1-1:1:4")
    assert opener.target_ref is None
    assert opener.groups[0].kind == "opening_basmala"

    shuba = editions.projection("shuba")
    assert shuba.project_range("1:1:1-1:1:4").target_ref == "1:1:1-1:1:4"


@has_editions
def test_provenance_pins_the_projection_the_shards_will_record():
    provenance = editions.provenance()
    assert provenance["reference_id"] == "qul-text-qpc-hafs-312"
    assert provenance["projection_id"]
    assert len(provenance["projection_sha256"]) == 64
