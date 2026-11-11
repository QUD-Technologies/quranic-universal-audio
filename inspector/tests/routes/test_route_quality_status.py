"""``POST /api/quality-status/<slug>`` — manual recitation quality rating.

Set needs ``reciter.set_quality_status`` (maintainer default) and a published
or marked-ready row; clear works in any state. Every change is an audit-only
``reciter.quality_set`` transition carrying the actor and previous value.
"""

from __future__ import annotations

import json

from tests.conftest import _seed_state

_ORIGIN = {"Origin": "http://localhost"}


def _seed(slug: str = "test_slug", **kw) -> None:
    spec = dict(
        slug=slug,
        state="released",
        assignee_hf_id=None,
        assignee_login="test_user",
        marked_ready=False,
        visibility="public",
    )
    spec.update(kw)
    _seed_state(**spec)


def _post(client, value, slug: str = "test_slug"):
    return client.post(
        f"/api/quality-status/{slug}",
        headers=_ORIGIN,
        json={"quality_status": value},
    )


def test_anonymous_gets_401(flask_client, state_persistence):
    _seed()
    assert _post(flask_client, "clean").status_code == 401


def test_contributor_is_forbidden(signed_in_client, state_persistence):
    _seed()
    client, _ = signed_in_client(role="contributor")
    assert _post(client, "clean").status_code == 403


def test_maintainer_sets_on_released_row(signed_in_client, state_persistence):
    _seed()
    client, _ = signed_in_client(role="maintainer")
    resp = _post(client, "almost_clean")
    assert resp.status_code == 200
    assert json.loads(resp.data)["quality_status"] == "almost_clean"

    from services import state as state_service

    assert state_service.get_row("test_slug").quality_status.value == "almost_clean"


def test_maintainer_sets_on_marked_ready_row(signed_in_client, state_persistence):
    _seed(state="under_review", assignee_hf_id="u-9", assignee_login="bob", marked_ready=True)
    client, _ = signed_in_client(role="maintainer")
    assert _post(client, "needs_work").status_code == 200


def test_set_rejected_on_unfinished_row(signed_in_client, state_persistence):
    _seed(state="under_review", assignee_hf_id="u-9", assignee_login="bob")
    client, _ = signed_in_client(role="maintainer")
    resp = _post(client, "clean")
    assert resp.status_code == 400
    assert json.loads(resp.data)["code"] == "STATE_PRECONDITION"


def test_clear_allowed_in_any_state(signed_in_client, state_persistence):
    _seed()
    client, _ = signed_in_client(role="maintainer")
    assert _post(client, "clean").status_code == 200

    from services.db import repo_state

    # Back in review since: the rating survives, and can still be cleared.
    _seed(state="awaiting_review")
    assert repo_state.get_row("test_slug").quality_status.value == "clean"
    resp = _post(client, None)
    assert resp.status_code == 200
    assert json.loads(resp.data)["quality_status"] is None


def test_unknown_value_is_400(signed_in_client, state_persistence):
    _seed()
    client, _ = signed_in_client(role="maintainer")
    resp = _post(client, "perfect")
    assert resp.status_code == 400
    assert json.loads(resp.data)["code"] == "QUALITY_STATUS_PAYLOAD"


def test_change_is_audited_with_actor_and_previous(signed_in_client, state_persistence):
    _seed()
    client, user = signed_in_client(role="maintainer", hf_user_id="m-1", login="mia")
    _post(client, "clean")
    _post(client, "almost_clean")

    from services.db import repo_transitions

    rows = [
        r for r in repo_transitions.for_slug("test_slug") if r["event"] == "reciter.quality_set"
    ]
    assert [r["payload"] for r in rows] == [
        {"quality_status": "clean", "previous": None},
        {"quality_status": "almost_clean", "previous": "clean"},
    ]
    assert rows[-1]["actor"]["hf_user_id"] == "m-1"


def test_predicates_reflect_gate(signed_in_client, state_persistence):
    _seed()
    client, _ = signed_in_client(role="maintainer")
    preds = json.loads(client.get("/api/reciter-task/test_slug").data)["predicates"]
    assert preds["can_set_quality_status"] is True
    assert preds["can_clear_quality_status"] is False  # nothing set yet
