"""GH release title carries the recitation count next to the version."""

from __future__ import annotations

from qua_jobs import cut_release


def test_release_name_includes_recitation_count():
    assert cut_release._release_name("v3.1.0", 57) == "v3.1.0 — 57 Recitations"


def test_release_name_singular():
    assert cut_release._release_name("v0.1.0", 1) == "v0.1.0 — 1 Recitation"


def test_create_release_posts_the_title(monkeypatch):
    sent: dict = {}

    def fake_request(method, path, token, json_body: dict | None = None, **_):
        sent.update(json_body or {})
        return {"upload_url": "u"}

    monkeypatch.setattr(cut_release, "_gh_request", fake_request)
    cut_release._gh_create_release(
        "o", "r", "v3.1.0", "body", "t", name=cut_release._release_name("v3.1.0", 57)
    )
    assert sent["tag_name"] == "v3.1.0"
    assert sent["name"] == "v3.1.0 — 57 Recitations"
