"""``services.reference.quran_refs`` — the per-edition static reference bundle."""

from __future__ import annotations

import orjson
import pytest

from services import data_loader, quran_refs
from services.reference import editions

has_editions = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)


@pytest.fixture(autouse=True)
def _clean_payload_cache():
    quran_refs.reset_cache()
    yield
    quran_refs.reset_cache()
    editions.clear_caches()


@pytest.fixture
def stub_hafs(monkeypatch):
    """A three-word Hafs corpus, one entry of which is a verse-end marker."""
    # None is the unset sentinel the consumer checks for (``is not None``); the
    # setter's dict annotation is stricter than the runtime contract.
    data_loader.cache.set_dk_words_flat_cache(None)  # type: ignore[reportArgumentType]
    monkeypatch.setattr(
        data_loader,
        "load_dk",
        lambda: {
            "1:1:1": {"text": "بِسْمِ"},
            "1:1:5": {"text": "۝١"},
            "7:1:1": {"text": "المص"},
        },
    )
    monkeypatch.setattr(
        quran_refs, "get_word_counts", lambda riwayah="hafs": {(1, 1): 4, (7, 1): 1}
    )


def test_build_payload_strips_verse_markers(stub_hafs):
    body = orjson.loads(quran_refs.build_payload())
    assert "1:1:1" in body["dk_words"]
    assert "7:1:1" in body["dk_words"]
    assert "1:1:5" not in body["dk_words"], "verse-end markers must be stripped"
    assert body["verse_word_counts"] == {"1:1": 4, "7:1": 1}


def test_payload_names_its_edition_and_its_verse_marker(stub_hafs):
    # The FE renders `verse_marker_prefix + arabic_digits`, so the prefix has to
    # travel with the bundle rather than being hardcoded per tab.
    body = orjson.loads(quran_refs.build_payload())
    assert body["riwayah"] == "hafs"
    assert body["verse_marker_prefix"] == "۝"


def test_payload_hash_is_deterministic(monkeypatch):
    data_loader.cache.set_dk_words_flat_cache(None)  # type: ignore[reportArgumentType]
    monkeypatch.setattr(data_loader, "load_dk", lambda: {"1:1:1": {"text": "X"}})
    monkeypatch.setattr(quran_refs, "get_word_counts", lambda riwayah="hafs": {(1, 1): 1})

    h1 = quran_refs.payload_hash()
    h2 = quran_refs.payload_hash()
    assert h1 == h2
    assert len(h1) == 12


# ---------------------------------------------------------------------------
# Non-Hafs editions
# ---------------------------------------------------------------------------


@has_editions
@pytest.mark.parametrize("riwayah", ("warsh", "qalun", "shuba"))
def test_the_qpc_fonts_get_no_verse_marker_prefix(riwayah):
    # The three packaged QPC fonts decorate the Arabic-Indic digits themselves.
    # Sending U+06DD as well renders two nested ornaments — the aligner app hit
    # exactly this, which is why the prefix is data and not a constant.
    body = orjson.loads(quran_refs.build_payload(riwayah))
    assert body["riwayah"] == riwayah
    assert body["verse_marker_prefix"] == ""


@has_editions
def test_each_edition_gets_its_own_payload_and_hash():
    hashes = {r: quran_refs.payload_hash(r) for r in ("hafs", "warsh", "qalun", "shuba")}
    assert len(set(hashes.values())) == 4, hashes
    # Warsh and Qalun share a script but not a bundle: the `riwayah` field
    # differs, so a browser can never be served one under the other's URL.
    assert hashes["warsh"] != hashes["qalun"]


@has_editions
def test_warsh_bundle_carries_the_medinan_verse_geometry():
    body = orjson.loads(quran_refs.build_payload("warsh"))
    counts = body["verse_word_counts"]
    assert len(counts) == 6214
    assert len(body["dk_words"]) == 77428
    # Al-Baqarah ends at 285 here, and its opening verse is not the muqattaat
    # alone — both facts a Hafs bundle would get wrong.
    assert "2:286" not in counts
    assert counts["2:285"] > 0
    assert counts["2:1"] > 1


@has_editions
def test_a_verse_text_resolves_in_the_editions_own_script():
    # 1:1:1 is the Basmala's first word in Hafs and al-Hamd in Warsh, because
    # Warsh does not number the Basmala.
    assert quran_refs.dk_text_for_ref("1:1:1-1:1:1", "warsh") != quran_refs.dk_text_for_ref(
        "1:1:1-1:1:1"
    )
    assert quran_refs.dk_text_for_ref("1:1:1-1:1:4", "warsh").count(" ") == 3


def test_a_hafs_opener_row_shows_the_dk_words_it_is_read_as(monkeypatch):
    data_loader.cache.set_dk_words_flat_cache(None)  # type: ignore[reportArgumentType]
    basmala = {f"1:1:{n}": {"text": f"b{n}"} for n in range(1, 5)}
    monkeypatch.setattr(data_loader, "load_dk", lambda: basmala)
    monkeypatch.setattr(quran_refs, "get_word_counts", lambda riwayah="hafs": {(1, 1): 4})
    body = orjson.loads(quran_refs.build_payload())
    # The Isti'adha's words are missing from this stub corpus, so it carries no text.
    assert body["special_texts"] == {"Basmala": "b1 b2 b3 b4"}


@has_editions
def test_a_warsh_opener_row_shows_the_editions_own_text():
    texts = quran_refs.special_texts("warsh")
    assert texts == {name: editions.special(name, "warsh") for name in ("Isti'adha", "Basmala")}
    assert texts["Basmala"] != quran_refs.special_texts("hafs")["Basmala"]
