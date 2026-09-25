"""``/api/admin/intake/<id>/plan|align`` — capability gating + error mapping."""

from __future__ import annotations

import json

import pytest

from qua_shared.schemas import IntakeAlignResponse, IntakePlanView
from services.admin import intake as intake_service
from services.admin.intake_plan import mint as intake_mint
from services.admin.intake_plan import plan as intake_plan

_HEADERS = {"Origin": "http://localhost", "Content-Type": "application/json"}
_VIEW = IntakePlanView(
    status="ready", created_at="2026-09-25T00:00:00Z", updated_at="2026-09-25T00:00:00Z"
)


@pytest.fixture
def stub_plan(monkeypatch):
    calls: list[tuple] = []

    def _update(rid, body):
        calls.append(("update", rid, body.identity.slug))
        return _VIEW

    def _mint(rid, actor, *, device, exempt):
        calls.append(("mint", rid, device, exempt))
        return IntakeAlignResponse(slug="rec_new", state="awaiting_alignment", align_started=True)

    monkeypatch.setattr(intake_plan, "get", lambda rid: None)
    monkeypatch.setattr(intake_plan, "build", lambda rid: _VIEW)
    monkeypatch.setattr(intake_plan, "update", _update)
    monkeypatch.setattr(intake_mint, "mint_and_align", _mint)
    return calls


def test_anonymous_is_401(flask_client, stub_plan):
    assert flask_client.get("/api/admin/intake/rq_1/plan").status_code == 401


@pytest.mark.parametrize("role", ["contributor", "maintainer"])
def test_non_owner_is_403(signed_in_client, stub_plan, role):
    client, _ = signed_in_client(role=role)
    assert client.get("/api/admin/intake/rq_1/plan").status_code == 403
    assert (
        client.post("/api/admin/intake/rq_1/align", headers=_HEADERS, data="{}").status_code == 403
    )
    assert stub_plan == []


def test_owner_reads_builds_edits_and_aligns(signed_in_client, stub_plan):
    client, _ = signed_in_client(role="owner")
    res = client.get("/api/admin/intake/rq_1/plan")
    assert res.status_code == 200 and res.get_json() == {"plan": None}
    assert res.headers["Cache-Control"] == "no-store"

    res = client.post("/api/admin/intake/rq_1/plan", headers=_HEADERS)
    assert res.status_code == 202 and res.get_json()["status"] == "ready"

    body = {"entries": [{"key": "e1", "include": False}], "identity": {"slug": "rec_new"}}
    res = client.put("/api/admin/intake/rq_1/plan", headers=_HEADERS, data=json.dumps(body))
    assert res.status_code == 200

    res = client.post(
        "/api/admin/intake/rq_1/align", headers=_HEADERS, data=json.dumps({"device": "CPU"})
    )
    assert res.status_code == 201 and res.get_json()["slug"] == "rec_new"
    assert stub_plan == [("update", "rq_1", "rec_new"), ("mint", "rq_1", "CPU", True)]


def test_bad_bodies_are_400(signed_in_client, stub_plan):
    client, _ = signed_in_client(role="owner")
    res = client.put(
        "/api/admin/intake/rq_1/plan", headers=_HEADERS, data=json.dumps({"entries": []})
    )
    assert res.status_code == 400
    res = client.post(
        "/api/admin/intake/rq_1/align", headers=_HEADERS, data=json.dumps({"device": "TPU"})
    )
    assert res.status_code == 400
    assert stub_plan == []


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (intake_plan.PlanError("request is accepted, not a pending intake", 409), 409),
        (intake_service.IngestSlugCollision("slug taken"), 409),
        (intake_service.IngestVocabMissing("unknown channel"), 422),
    ],
)
def test_service_refusals_map_to_status(signed_in_client, stub_plan, monkeypatch, exc, status):
    def _raise(*_a, **_kw):
        raise exc

    monkeypatch.setattr(intake_mint, "mint_and_align", _raise)
    client, _ = signed_in_client(role="owner")
    res = client.post("/api/admin/intake/rq_1/align", headers=_HEADERS, data="{}")
    assert res.status_code == status and res.get_json()["error"] == str(exc)
