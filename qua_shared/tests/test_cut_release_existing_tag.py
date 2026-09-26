"""Regression: a cut must detect a GH tag the Inspector ledger never recorded.

v3.1.0 shipped to GitHub but its completion webhook got a 403, so the ledger
stayed at v3.0.0 and the preview kept proposing v3.1.0 again.
"""

from __future__ import annotations

import pytest

from qua_jobs import cut_release


def _fake_request(error: str | None):
    def _req(method, path, token, **_kw):
        if error:
            raise RuntimeError(error)
        return {"tag_name": "v3.1.0"}

    return _req


def test_existing_tag_detected(monkeypatch):
    monkeypatch.setattr(cut_release, "_gh_request", _fake_request(None))
    assert cut_release._gh_release_exists("o", "r", "v3.1.0", "t") is True


def test_missing_tag_is_404(monkeypatch):
    monkeypatch.setattr(cut_release, "_gh_request", _fake_request("GH API GET x → 404: Not Found"))
    assert cut_release._gh_release_exists("o", "r", "v3.2.0", "t") is False


def test_other_errors_propagate(monkeypatch):
    monkeypatch.setattr(cut_release, "_gh_request", _fake_request("GH API GET x → 500: boom"))
    with pytest.raises(RuntimeError):
        cut_release._gh_release_exists("o", "r", "v3.2.0", "t")
