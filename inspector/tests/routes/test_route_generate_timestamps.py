"""HTTP boundary tests for ``POST /api/admin/generate-timestamps/<slug>``.

The route launches the in-container MFA timestamps job. It accepts two entry
states — a marked-ready ``under_review`` row (first publish) and an already
``released`` row (regenerate) — and 409s any other state. The actual HF Job
launch + single-flight check are stubbed so we don't hit the network.
"""

from __future__ import annotations

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}
_URL = "/api/admin/generate-timestamps"


def _stub_launch(monkeypatch):
    """Stub the launcher + single-flight check so no HF Jobs call is made."""
    from services.admin import timestamps_jobs as ts_jobs

    monkeypatch.setattr(ts_jobs, "running_job_for", lambda slug: None)
    monkeypatch.setattr(
        ts_jobs,
        "launch",
        lambda slug, settings=None, full=False, webhook_base=None: {
            "job_id": "j_test",
            "url": None,
        },
    )


def test_generate_ts_on_released_row_launches(signed_in_client, monkeypatch, seed_state):
    """Regen path: a released reciter accepts a fresh generate-timestamps run."""
    client, _user = signed_in_client(role="maintainer")
    _stub_launch(monkeypatch)
    seed_state("rec_a", state="released")

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS)

    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json() == {"job_id": "j_test", "url": None}


def test_generate_ts_on_marked_ready_launches(signed_in_client, monkeypatch, seed_state):
    """First-publish path stays intact: marked-ready under_review launches."""
    client, _user = signed_in_client(role="maintainer")
    _stub_launch(monkeypatch)
    seed_state("rec_a", state="under_review", assignee_hf_id="u-rev", marked_ready=True)

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS)

    assert resp.status_code == 202, resp.get_data(as_text=True)


def test_generate_ts_rejects_under_review_not_marked_ready(signed_in_client, seed_state):
    """An under_review row that isn't marked ready has nothing to publish → 409."""
    client, _user = signed_in_client(role="maintainer")
    seed_state("rec_a", state="under_review", assignee_hf_id="u-rev")

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS)

    assert resp.status_code == 409
    assert resp.get_json()["state"] == "under_review"


def test_generate_ts_rejects_awaiting_review(signed_in_client, seed_state):
    """A pre-claim state can't launch — the gate keeps the API honest."""
    client, _user = signed_in_client(role="maintainer")
    seed_state("rec_a", state="awaiting_review")

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS)

    assert resp.status_code == 409


def test_generate_ts_unknown_slug_404(signed_in_client):
    client, _user = signed_in_client(role="maintainer")

    resp = client.post(f"{_URL}/nope", headers=_HEADERS)

    assert resp.status_code == 404


def test_generate_ts_scopes_to_affected_chapters(signed_in_client, monkeypatch, seed_state):
    """An affected-only regen threads ``chapters`` into the launch settings."""
    from services.admin import timestamps_jobs as ts_jobs

    client, _user = signed_in_client(role="maintainer")
    seed_state("rec_a", state="released")
    monkeypatch.setattr(ts_jobs, "running_job_for", lambda slug: None)
    captured: dict = {}

    def _capture(slug, settings=None, full=False, webhook_base=None):
        assert settings is not None
        captured["chapters"] = settings.chapters
        captured["full"] = full
        return {"job_id": "j_test", "url": None}

    monkeypatch.setattr(ts_jobs, "launch", _capture)

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS, json={"chapters": [12, 5, 5]})

    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert captured == {"chapters": [5, 12], "full": False}  # deduped + sorted

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS, json={"full": True})

    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert captured == {"chapters": None, "full": True}


def test_generate_ts_rejects_a_non_boolean_full(signed_in_client, monkeypatch, seed_state):
    client, _user = signed_in_client(role="maintainer")
    _stub_launch(monkeypatch)
    seed_state("rec_a", state="released")

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS, json={"full": "yes"})

    assert resp.status_code == 400


def test_generate_ts_rejects_out_of_range_chapters(signed_in_client, monkeypatch, seed_state):
    client, _user = signed_in_client(role="maintainer")
    _stub_launch(monkeypatch)
    seed_state("rec_a", state="released")

    resp = client.post(f"{_URL}/rec_a", headers=_HEADERS, json={"chapters": [0, 200]})

    assert resp.status_code == 400
    assert "chapters" in resp.get_json()["error"]
