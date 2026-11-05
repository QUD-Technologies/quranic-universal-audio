"""Flask app smoke test.

Imports the module-level ``app`` from ``inspector/app.py`` and verifies that
the ``/api/surah-info`` route responds 200 with a JSON payload. Chosen as the
smoke target because it's a cross-tab route with no DB / disk-state
dependencies — it just returns ``load_surah_info_lite()``.

Import prerequisites handled in ``pyproject.toml`` (``pythonpath = ["."]``).
"""

import pytest


@pytest.fixture(scope="module")
def client():
    # Import lazily so an import error shows up as a test failure rather than
    # a collection-time crash (helps keep the rest of the suite green while
    # this target is iterated on).
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


def test_surah_info_returns_json(client):
    res = client.get("/api/surah-info")
    assert res.status_code == 200
    assert res.is_json

    payload = res.get_json()
    assert isinstance(payload, dict)
    # Sanity: at least one surah key should be present. `load_surah_info_lite`
    # reads `qua_shared.quran_text.surah_info()`; shape is `{"1": {...}, ..., "114": {...}}`.
    assert len(payload) > 0


def test_404_on_unknown_route(client):
    res = client.get("/api/definitely-not-a-real-route")
    # The app's JSON error handler turns HTTPExceptions into JSON 404s, but
    # any 4xx is acceptable for a smoke test — we're just confirming the
    # Flask stack is wired up.
    assert 400 <= res.status_code < 500
