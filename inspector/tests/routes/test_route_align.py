"""``/api/admin/reciter/<slug>/align*`` — capability gating + envelope mapping."""

from __future__ import annotations

import json

import pytest

from qua_shared.schemas import AlignRunStatus

_HEADERS = {"Origin": "http://localhost", "Content-Type": "application/json"}


def _status(**over):
    base = {
        "run_id": "r1",
        "slug": "rec_x",
        "stage": "acquire",
        "status": "pending",
        "started_at": "2026-09-12T00:00:00Z",
        "updated_at": "2026-09-12T00:00:00Z",
    }
    return AlignRunStatus(**{**base, **over})


@pytest.fixture
def stub_runs(monkeypatch):
    from services.admin.align_pipeline import runs

    calls: list[tuple] = []

    def _start(slug, actor, *, model_name, device, exempt):
        calls.append(("start", slug, actor.hf_user_id, model_name, device, exempt))
        return _status(slug=slug)

    monkeypatch.setattr(runs, "start", _start)
    monkeypatch.setattr(runs, "status_for_slug", lambda slug: _status(slug=slug, status="running"))
    monkeypatch.setattr(runs, "retry", lambda slug, actor: _status(slug=slug, attempt=2))
    monkeypatch.setattr(runs, "cancel", lambda slug, actor: _status(slug=slug, status="canceled"))
    return calls


def test_anonymous_is_401(flask_client, stub_runs):
    res = flask_client.post("/api/admin/reciter/rec_x/align", headers=_HEADERS, data="{}")
    assert res.status_code == 401


def test_contributor_is_403(signed_in_client, stub_runs):
    client, _ = signed_in_client(role="contributor")
    res = client.post("/api/admin/reciter/rec_x/align", headers=_HEADERS, data="{}")
    assert res.status_code == 403
    assert client.get("/api/admin/reciter/rec_x/align/status").status_code == 403


def test_maintainer_can_start_and_read_status(signed_in_client, stub_runs):
    client, user = signed_in_client(role="maintainer")
    res = client.post(
        "/api/admin/reciter/rec_x/align",
        headers=_HEADERS,
        data=json.dumps({"model_name": "Base", "device": "CPU"}),
    )
    assert res.status_code == 202, res.get_json()
    assert res.get_json()["stage"] == "acquire"
    # A maintainer is inside the shared align budget by default.
    assert stub_runs == [("start", "rec_x", user["hf_user_id"], "Base", "CPU", False)]

    res = client.get("/api/admin/reciter/rec_x/align/status")
    assert res.status_code == 200 and res.get_json()["status"] == "running"
    assert (
        client.post("/api/admin/reciter/rec_x/align/retry", headers=_HEADERS).get_json()["attempt"]
        == 2
    )
    assert (
        client.post("/api/admin/reciter/rec_x/align/cancel", headers=_HEADERS).get_json()["status"]
        == "canceled"
    )


def test_bad_body_and_service_refusal_map_to_errors(signed_in_client, stub_runs, monkeypatch):
    from services.admin.align_pipeline import runs

    client, _ = signed_in_client(role="owner")
    res = client.post(
        "/api/admin/reciter/rec_x/align", headers=_HEADERS, data=json.dumps({"model_name": "Huge"})
    )
    assert res.status_code == 400
    res = client.post(
        "/api/admin/reciter/rec_x/align", headers=_HEADERS, data=json.dumps({"device": "TPU"})
    )
    assert res.status_code == 400

    def _refuse(slug, actor, **_kw):
        raise runs.AlignRunError("busy", 409)

    monkeypatch.setattr(runs, "start", _refuse)
    res = client.post("/api/admin/reciter/rec_x/align", headers=_HEADERS, data="{}")
    assert res.status_code == 409 and res.get_json() == {"error": "busy"}


def test_owner_bearer_token_drives_a_run(flask_client, stub_runs, monkeypatch):
    from qua_shared.schemas import Actor, Role
    from services.auth import token_auth

    owner = Actor(hf_user_id="u-owner", login_at_time="owner", role=Role.OWNER)
    monkeypatch.setattr(token_auth, "resolve_owner_from_token", lambda token: owner)
    res = flask_client.post(
        "/api/admin/reciter/rec_x/align",
        headers={"Authorization": "Bearer hf_x", "Content-Type": "application/json"},
        data="{}",
    )
    assert res.status_code == 202 and stub_runs[-1][2] == "u-owner"
    assert stub_runs[-1][5] is True  # an owner bearer bypasses the shared budget


def test_owner_session_is_exempt(signed_in_client, stub_runs):
    client, _ = signed_in_client(role="owner")
    res = client.post("/api/admin/reciter/rec_x/align", headers=_HEADERS, data="{}")
    assert res.status_code == 202 and stub_runs[-1][5] is True


def test_limit_refusal_maps_to_429(signed_in_client, stub_runs, monkeypatch):
    from services.admin.align_pipeline import runs

    def _refuse(slug, actor, **_kw):
        raise runs.AlignRunError("GPU limit reached", 429)

    monkeypatch.setattr(runs, "start", _refuse)
    client, _ = signed_in_client(role="maintainer")
    res = client.post("/api/admin/reciter/rec_x/align", headers=_HEADERS, data="{}")
    assert res.status_code == 429 and res.get_json() == {"error": "GPU limit reached"}


def test_cookie_post_without_origin_is_rejected(signed_in_client, stub_runs):
    client, _ = signed_in_client(role="owner")
    res = client.post("/api/admin/reciter/rec_x/align", data="{}", content_type="application/json")
    assert res.status_code == 403
