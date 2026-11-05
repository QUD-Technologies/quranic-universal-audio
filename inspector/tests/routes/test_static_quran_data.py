"""/api/static/quran-data/<name> — the pinned pack and fonts, whitelisted, digest-ETagged."""

from __future__ import annotations

import pytest

from qua_shared import quran_text
from qua_shared.quran_data_pin import FONT_SHA256, PACK_NAME, PACK_SHA256


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    pack = tmp_path / PACK_NAME
    pack.write_bytes(b"pack-bytes")
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "DigitalKhattV2.otf").write_bytes(b"OTTO-font")
    monkeypatch.setenv(quran_text.PACK_ENV, str(pack))
    return tmp_path


def test_serves_the_pack_and_a_font_with_the_pinned_digest(flask_client, data_dir):
    resp = flask_client.get(f"/api/static/quran-data/{PACK_NAME}")
    assert resp.status_code == 200
    assert resp.data == b"pack-bytes"
    assert resp.headers["ETag"] == f'"{PACK_SHA256}"'
    font = flask_client.get("/api/static/quran-data/DigitalKhattV2.otf")
    assert font.status_code == 200
    assert font.mimetype == "font/otf"
    assert font.headers["ETag"] == f'"{FONT_SHA256["DigitalKhattV2.otf"]}"'
    assert "no-cache" in font.headers["Cache-Control"]


def test_revalidation_is_a_304(flask_client, data_dir):
    etag = f'"{FONT_SHA256["DigitalKhattV2.otf"]}"'
    resp = flask_client.get("/api/static/quran-data/DigitalKhattV2.otf", headers={"If-None-Match": etag})
    assert resp.status_code == 304


def test_unpinned_or_unfetched_names_are_404(flask_client, data_dir):
    assert flask_client.get("/api/static/quran-data/other.woff2").status_code == 404
    assert flask_client.get("/api/static/quran-data/..%2Fquran-core.qref").status_code == 404
    assert flask_client.get("/api/static/quran-data/SurahNameV2.woff2").status_code == 404
