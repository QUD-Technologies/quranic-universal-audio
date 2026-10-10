"""``GET /api/ts/profile/<reciter>``: the public recitation profile and its cache."""

from __future__ import annotations

import pytest

from services.storage.hf_bucket import StorageNotFound

STORED = {
    "schema_version": 1,
    "madd": {
        "tabii": {"n": 37054, "mean_ms": 349},
        "munfasil": {"n": 3098, "mean_ms": 1639, "verdict": "tawassut", "share": 0.94},
        "muttasil": {"n": 1744, "mean_ms": 1829},
        "lazim": {"n": 0, "mean_ms": None},
        "arid": {"n": 5733, "mean_ms": 1985, "verdict": "tawassut", "share": 0.85},
        "leen": {"n": 177, "mean_ms": 1632, "verdict": "ishbaa", "share": 0.97},
    },
    "ghunnah": {"n": 15899, "mean_ms": 919},
    "silence": {"n": 11094, "mean_ms": 453, "median_ms": 439},
    "future_key": {"anything": 1},
}


class _Backend:
    def __init__(self, files: dict[str, object]):
        self.files = files
        self.reads: list[str] = []

    def read_json(self, path: str):
        self.reads.append(path)
        if path not in self.files:
            raise StorageNotFound(path)
        return self.files[path]


@pytest.fixture
def profile_env(monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    from services.reference import recitation_profile

    backend = _Backend({"reciters/reciter_a/recitation_profile.json": STORED})
    monkeypatch.setattr(ts_routes.ts_serve, "is_viewable", lambda *a, **k: True)
    monkeypatch.setattr(recitation_profile, "is_hafs", lambda slug: True)
    monkeypatch.setattr(recitation_profile, "get_backend", lambda: backend)
    monkeypatch.setattr(
        recitation_profile.storage_paths,
        "recitation_profile_path",
        lambda slug: f"reciters/{slug}/recitation_profile.json",
    )
    recitation_profile.invalidate()
    yield backend
    recitation_profile.invalidate()


def test_profile_serves_the_public_projection(flask_client, profile_env):
    res = flask_client.get("/api/ts/profile/reciter_a")

    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "no-store"
    assert res.get_json() == {
        "madd": [
            {"kind": "tabii", "mean_ms": 349, "length": None},
            {"kind": "munfasil", "mean_ms": 1639, "length": "tawassut"},
            {"kind": "muttasil", "mean_ms": 1829, "length": None},
            {"kind": "arid", "mean_ms": 1985, "length": "tawassut"},
            {"kind": "leen", "mean_ms": 1632, "length": "ishbaa"},
        ],
        "ghunnah_ms": 919,
        "pause_ms": 453,
    }


def test_profile_missing_file_is_null(flask_client, profile_env):
    res = flask_client.get("/api/ts/profile/reciter_b")

    assert res.status_code == 200
    assert res.get_json() is None


def test_profile_non_hafs_is_null_without_a_read(flask_client, profile_env, monkeypatch):
    from services.reference import recitation_profile

    monkeypatch.setattr(recitation_profile, "is_hafs", lambda slug: False)

    assert flask_client.get("/api/ts/profile/reciter_a").get_json() is None
    assert profile_env.reads == []


def test_profile_hidden_reciter_is_404(flask_client, profile_env, monkeypatch):
    from routes.timestamps import timestamps as ts_routes

    monkeypatch.setattr(ts_routes.ts_serve, "is_viewable", lambda *a, **k: False)

    assert flask_client.get("/api/ts/profile/reciter_a").status_code == 404


def test_profile_is_cached_until_ts_refreshed(flask_client, profile_env, monkeypatch):
    from services.reference import readings

    monkeypatch.setenv("INSPECTOR_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setattr(readings, "refresh_in_background", lambda slug: None)
    path = "reciters/reciter_c/recitation_profile.json"

    assert flask_client.get("/api/ts/profile/reciter_c").get_json() is None
    profile_env.files[path] = STORED
    assert flask_client.get("/api/ts/profile/reciter_c").get_json() is None
    assert profile_env.reads == [path]

    flask_client.post(
        "/api/admin/internal/ts-refreshed",
        json={"slug": "reciter_c"},
        headers={"X-Inspector-Job-Secret": "s3cret"},
    )

    assert flask_client.get("/api/ts/profile/reciter_c").get_json()["pause_ms"] == 453
