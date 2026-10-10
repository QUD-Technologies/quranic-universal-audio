"""Static-data routes for the non-Hafs editions: refs bundle + paired font.

Skipped wholesale without ``qua_domain``; the degraded behaviour of those same
routes is pinned in ``test_static_quran_refs.py``, which runs everywhere.
"""

from __future__ import annotations

import hashlib

import pytest

from services.reference import editions

pytestmark = pytest.mark.skipif(
    not editions.available(), reason="qua-domain not installed (Hafs-only runtime)"
)

RIWAYAT = (("warsh_an_nafi", "warsh"), ("qalon_an_nafi", "qalun"), ("shubah_an_asim", "shuba"))


@pytest.fixture(autouse=True)
def _clean_caches():
    yield
    from services import quran_refs
    from services.storage import cache

    quran_refs.reset_cache()
    editions.clear_caches()


@pytest.mark.parametrize(("inspector_slug", "sdk_slug"), RIWAYAT)
def test_refs_bundle_is_served_under_the_inspector_vocabulary_slug(
    flask_client, tmp_reciter_dir, inspector_slug, sdk_slug
):
    # The FE holds the catalog's slug (`warsh_an_nafi`), not the SDK's
    # (`warsh`); the route is the conversion boundary.
    res = flask_client.get(f"/api/static/quran-refs.json?riwayah={inspector_slug}")
    assert res.status_code == 200
    body = res.get_json()
    assert body["riwayah"] == sdk_slug
    assert body["verse_marker_prefix"] == ""


@pytest.mark.parametrize(("inspector_slug", "sdk_slug"), RIWAYAT)
def test_version_and_payload_etag_agree_per_edition(
    flask_client, tmp_reciter_dir, inspector_slug, sdk_slug
):
    version = flask_client.get(
        f"/api/static/quran-refs/version?riwayah={inspector_slug}"
    ).get_json()["version"]
    res = flask_client.get(f"/api/static/quran-refs.json?riwayah={inspector_slug}")
    assert res.headers["ETag"] == f'"{version}"'
    hafs = flask_client.get("/api/static/quran-refs/version").get_json()["version"]
    assert version != hafs


@pytest.mark.parametrize(("inspector_slug", "sdk_slug"), RIWAYAT)
def test_font_is_served_with_its_own_digest_as_the_etag(
    flask_client, tmp_reciter_dir, inspector_slug, sdk_slug
):
    res = flask_client.get(f"/api/static/edition/{inspector_slug}/font")
    assert res.status_code == 200
    assert res.mimetype.startswith("font/")
    assert "immutable" in res.headers["Cache-Control"]
    digest = hashlib.sha256(res.data).hexdigest()
    assert res.headers["ETag"] == f'"{digest}"'
    assert f"{sdk_slug}." in res.headers["Content-Disposition"]


def test_a_runtime_without_the_package_refuses_rather_than_substituting_a_font(
    flask_client, tmp_reciter_dir, monkeypatch
):
    # A fallback font would render the edition's script with the wrong
    # ligatures and stop marks — worse than no page at all.
    monkeypatch.setattr(editions, "_module", lambda: None)
    editions.clear_caches()
    assert flask_client.get("/api/static/edition/warsh_an_nafi/font").status_code == 503


def test_seg_config_serves_each_editions_own_coordinate_vocabulary(flask_client, tmp_reciter_dir):
    hafs = flask_client.get("/api/seg/config").get_json()
    warsh = flask_client.get("/api/seg/config?riwayah=warsh_an_nafi").get_json()

    assert hafs["riwayah"] == "hafs_an_asim"
    assert warsh["riwayah"] == "warsh_an_nafi"
    # 30 muqattaat words in both, but Warsh merges two of them into one verse.
    assert len(warsh["muqattaat_words"]) == len(hafs["muqattaat_words"]) == 30
    assert [42, 1, 2] in warsh["muqattaat_words"]
    assert [42, 1, 2] not in hafs["muqattaat_words"]


def test_seg_config_rejects_an_unsupported_riwayah(flask_client, tmp_reciter_dir):
    assert flask_client.get("/api/seg/config?riwayah=duri_an_abi_amr").status_code == 400
