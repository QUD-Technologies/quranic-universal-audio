"""/api/static/mushaf-font/<name> — whitelisted, bucket-backed, immutable."""

from __future__ import annotations


def _put_font(root, name: str, body: bytes) -> None:
    """Drop a font where the filesystem backend (rooted at ``tmp_path``) serves it."""
    path = root / "reference" / "mushaf-fonts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_serves_a_whitelisted_font_from_the_bucket(flask_client, tmp_reciter_dir, tmp_path):
    _put_font(tmp_path, "juz-font.woff2", b"wOF2-fake")
    resp = flask_client.get("/api/static/mushaf-font/juz-font.woff2")
    assert resp.status_code == 200
    assert resp.data == b"wOF2-fake"
    assert resp.mimetype == "font/woff2"
    assert "immutable" in resp.headers["Cache-Control"]


def test_rejects_a_name_outside_the_whitelist(flask_client, tmp_reciter_dir, tmp_path):
    _put_font(tmp_path, "other.woff2", b"x")
    assert flask_client.get("/api/static/mushaf-font/other.woff2").status_code == 404
    assert flask_client.get("/api/static/mushaf-font/..%2Fdb%2Finspector.db").status_code == 404


def test_missing_bucket_object_is_a_404(flask_client, tmp_reciter_dir):
    assert flask_client.get("/api/static/mushaf-font/surah-name-v2.woff2").status_code == 404
