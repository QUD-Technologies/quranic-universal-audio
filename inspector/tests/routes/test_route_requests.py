"""Tests for the request-flow routes.

Covers all four routes under ``inspector/routes/requests.py``:

- ``POST /api/reciter/<slug>/request`` — contributor+
- ``GET  /api/admin/request/<slug>`` — maintainer+ (with tier-aware actor redaction)
- ``POST /api/admin/request/<slug>/reject-soft`` — owner-only
- ``POST /api/admin/request/<slug>/reject-hard`` — owner-only
- ``POST /api/admin/reciter/<slug>/undiscard`` — owner-only
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone

import pytest

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_backend(tmp_path, monkeypatch):
    """Per-test FilesystemBackend so route writes never reach the real bucket."""
    from qua_shared.schemas import (
        AudioCategory,
        Channel,
        Delivery,
        ReciterEntry,
        Riwayah,
        Source,
        Style,
        Vocab,
    )
    from services import hf_bucket as _hf_bucket
    from services import pending_requests as pending_requests_service

    monkeypatch.setenv("INSPECTOR_BACKEND", "filesystem")
    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))
    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)

    # Seed the catalog (3 deliveries) + state rows into the SQLite substrate.
    from services import db
    from services.db import repo_catalog
    from tests.conftest import _seed_state

    now = datetime.now(UTC)
    vocab = Vocab(
        riwayat=[
            Riwayah(slug="hafs", short="H", name="Hafs"),
            Riwayah(slug="warsh", short="W", name="Warsh"),
        ],
        styles=[Style(slug="murattal", short="M", name="Murattal")],
        sources=[Source(slug="src1", name="Source One")],
        channels=[Channel(slug="ch1", short="c1", name="Channel One")],
    )
    with db.transaction():
        repo_catalog.load_vocab(vocab)
        for rid, name in (
            ("rec_clean", "Clean Reciter"),
            ("rec_pending", "Pending Reciter"),
            ("rec_discarded", "Discarded Reciter"),
        ):
            repo_catalog.insert_reciter(ReciterEntry(reciter_id=rid, name_en=name))
            repo_catalog.insert_delivery(
                Delivery(
                    slug=rid,
                    reciter_id=rid,
                    riwayah="hafs",
                    style="murattal",
                    source="src1",
                    channel="ch1",
                    audio_category=AudioCategory.BY_SURAH,
                    chapter_count=114,
                    added_at=now,
                    added_by_hf_id="seed",
                )
            )

    _seed_state("rec_clean", state="catalogued", reciter_id="rec_clean")
    _seed_state("rec_pending", state="awaiting_alignment", reciter_id="rec_pending")
    _seed_state(
        "rec_discarded",
        state="catalogued",
        visibility="discarded",
        visibility_reason="testing setup",
        reciter_id="rec_discarded",
    )

    # Seed an in-flight pending entry for rec_pending so admin GET + reject
    # tests have something to inspect.
    from qua_shared.schemas import Actor, ProposedEdits, Role

    pending_requests_service.submit(
        "rec_pending",
        requester=Actor(
            hf_user_id="u-requester",
            login_at_time="requester",
            role=Role.CONTRIBUTOR,
        ),
        edits=ProposedEdits(name_en="Renamed", recording_year=2024),
        comments="Pre-seeded pending request for tests.",
    )

    yield backend

    _hf_bucket.reset_backend()


# ---------------------------------------------------------------------------
# POST /api/reciter/<slug>/request
# ---------------------------------------------------------------------------


def test_submit_anonymous_returns_401(flask_client):
    res = flask_client.post(
        "/api/reciter/rec_clean/request",
        headers=_HEADERS,
        data=json.dumps({"proposed_edits": {}, "comments": None}),
    )
    assert res.status_code == 401


def test_submit_contributor_happy_path(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1", login="alice")
    res = client.post(
        "/api/reciter/rec_clean/request",
        headers=_HEADERS,
        data=json.dumps(
            {
                "proposed_edits": {"name_en": "Cleaner Name", "recording_year": 2023},
                "comments": "Found a better recording.",
            }
        ),
    )
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["state"] == "awaiting_alignment"

    from services import pending_requests as pending_requests_service

    pending = pending_requests_service.get("rec_clean")
    assert pending is not None
    assert pending.proposed_edits.name_en == "Cleaner Name"
    assert pending.requester.hf_user_id == "u-1"

    # The submit handler drives state.transition("reciter.requested"); assert
    # the canonical event-log row actually landed in the transitions table
    # (regression guard: a future refactor that bypasses audit.append would
    # have silently passed under the previous global-monkeypatch fixture).
    from services.db import repo_transitions

    events = [r["event"] for r in repo_transitions.for_slug("rec_clean")]
    assert "reciter.requested" in events


def test_submit_rejects_when_already_pending(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    res = client.post(
        "/api/reciter/rec_pending/request",
        headers=_HEADERS,
        data=json.dumps({"proposed_edits": {}, "comments": None}),
    )
    assert res.status_code == 409


def test_submit_rejects_unknown_reciter(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    res = client.post(
        "/api/reciter/nope/request",
        headers=_HEADERS,
        data=json.dumps({"proposed_edits": {}, "comments": None}),
    )
    assert res.status_code == 404


def test_submit_rejects_discarded(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    res = client.post(
        "/api/reciter/rec_discarded/request",
        headers=_HEADERS,
        data=json.dumps({"proposed_edits": {}, "comments": None}),
    )
    assert res.status_code == 400


def test_submit_rejects_bad_year(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    res = client.post(
        "/api/reciter/rec_clean/request",
        headers=_HEADERS,
        data=json.dumps(
            {
                "proposed_edits": {"recording_year": 1700},
                "comments": None,
            }
        ),
    )
    assert res.status_code == 400


def test_submit_rejects_bad_origin(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    res = client.post(
        "/api/reciter/rec_clean/request",
        headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
        data=json.dumps({"proposed_edits": {}, "comments": None}),
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/request/<slug>
# ---------------------------------------------------------------------------


def test_get_pending_anonymous_returns_401(flask_client):
    res = flask_client.get("/api/admin/request/rec_pending")
    assert res.status_code == 401


def test_get_pending_contributor_returns_403(signed_in_client):
    client, _ = signed_in_client(role="contributor")
    res = client.get("/api/admin/request/rec_pending")
    assert res.status_code == 403


def test_get_pending_maintainer_redacts_requester_login(signed_in_client):
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.get("/api/admin/request/rec_pending")
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["slug"] == "rec_pending"
    assert body["proposed_edits"]["name_en"] == "Renamed"
    assert "requester_login" not in body
    assert "requester_hf_user_id" not in body
    assert body["requester_role"] == "contributor"


def test_get_pending_owner_includes_requester_login(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.get("/api/admin/request/rec_pending")
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["requester_login"] == "requester"
    assert body["requester_hf_user_id"] == "u-requester"


def test_get_pending_404_when_no_pending(signed_in_client):
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.get("/api/admin/request/rec_clean")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/request/<slug>/reject-soft
# ---------------------------------------------------------------------------


def test_reject_soft_contributor_returns_403(signed_in_client):
    client, _ = signed_in_client(role="contributor")
    res = client.post(
        "/api/admin/request/rec_pending/reject-soft",
        headers=_HEADERS,
        data=json.dumps({"reason": "ten chars+ reason"}),
    )
    assert res.status_code == 403


def test_reject_soft_maintainer_returns_403(signed_in_client):
    """Send-back is owner-only now — maintainers are denied."""
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.post(
        "/api/admin/request/rec_pending/reject-soft",
        headers=_HEADERS,
        data=json.dumps({"reason": "not a priority right now"}),
    )
    assert res.status_code == 403


def test_reject_soft_owner_happy_path(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/request/rec_pending/reject-soft",
        headers=_HEADERS,
        data=json.dumps({"reason": "not a priority right now"}),
    )
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["state"] == "catalogued"

    from services import pending_requests as pending_requests_service
    from services import state as state_service

    assert pending_requests_service.get("rec_pending") is None
    row = state_service.get_row("rec_pending")
    assert row is not None
    assert row.visibility.value == "public"


def test_reject_soft_requires_reason(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/request/rec_pending/reject-soft",
        headers=_HEADERS,
        data=json.dumps({"reason": "short"}),
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/admin/request/<slug>/reject-hard
# ---------------------------------------------------------------------------


def test_reject_hard_contributor_returns_403(signed_in_client):
    client, _ = signed_in_client(role="contributor")
    res = client.post(
        "/api/admin/request/rec_pending/reject-hard",
        headers=_HEADERS,
        data=json.dumps({"reason": "duplicate of another reciter"}),
    )
    assert res.status_code == 403


def test_reject_hard_maintainer_returns_403(signed_in_client):
    """Discard is owner-only now — maintainers are denied."""
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.post(
        "/api/admin/request/rec_pending/reject-hard",
        headers=_HEADERS,
        data=json.dumps({"reason": "duplicate of an already-published reciter"}),
    )
    assert res.status_code == 403


def test_reject_hard_owner_sets_discarded(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/request/rec_pending/reject-hard",
        headers=_HEADERS,
        data=json.dumps({"reason": "duplicate of an already-published reciter"}),
    )
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["state"] == "catalogued"

    from services import state as state_service

    row = state_service.get_row("rec_pending")
    assert row is not None
    assert row.visibility.value == "discarded"
    assert row.visibility_reason == "duplicate of an already-published reciter"


def test_reject_hard_requires_reason(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/request/rec_pending/reject-hard",
        headers=_HEADERS,
        data=json.dumps({"reason": "short"}),
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/admin/reciter/<slug>/undiscard
# ---------------------------------------------------------------------------


def test_undiscard_anonymous_returns_401(flask_client):
    res = flask_client.post(
        "/api/admin/reciter/rec_discarded/undiscard",
        headers=_HEADERS,
        data=json.dumps({"reason": "restoring per user request"}),
    )
    assert res.status_code == 401


def test_undiscard_maintainer_returns_403(signed_in_client):
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.post(
        "/api/admin/reciter/rec_discarded/undiscard",
        headers=_HEADERS,
        data=json.dumps({"reason": "restoring per user request"}),
    )
    assert res.status_code == 403


def test_undiscard_owner_happy_path(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/reciter/rec_discarded/undiscard",
        headers=_HEADERS,
        data=json.dumps({"reason": "restoring per user request"}),
    )
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body["visibility"] == "public"

    from services import state as state_service

    row = state_service.get_row("rec_discarded")
    assert row is not None
    assert row.visibility.value == "public"


def test_undiscard_requires_reason(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.post(
        "/api/admin/reciter/rec_discarded/undiscard",
        headers=_HEADERS,
        data=json.dumps({"reason": "short"}),
    )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/admin/requests  (Requests-tab list)
# ---------------------------------------------------------------------------


def test_list_requests_contributor_returns_403(signed_in_client):
    client, _ = signed_in_client(role="contributor")
    res = client.get("/api/admin/requests?status=open")
    assert res.status_code == 403


def test_list_requests_invalid_status_returns_400(signed_in_client):
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.get("/api/admin/requests?status=bogus")
    assert res.status_code == 400


def test_list_requests_maintainer_open_redacts_and_diffs(signed_in_client):
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    res = client.get("/api/admin/requests?status=open")
    assert res.status_code == 200
    body = json.loads(res.data)

    assert body["counts"]["open"] == 1
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["slug"] == "rec_pending"
    # maintainer: requester identity redacted, role retained
    assert "requester_login" not in row
    assert row["requester_role"] == "contributor"
    # proposed-changes diff over the seeded ProposedEdits (name_en + year)
    changed = {c["field"]: c for c in row["changes"]}
    assert changed["name_en"]["from"] == "Pending Reciter"
    assert changed["name_en"]["to"] == "Renamed"
    assert changed["recording_year"]["to"] == 2024


def test_list_requests_owner_includes_requester_login(signed_in_client):
    client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = client.get("/api/admin/requests?status=open")
    assert res.status_code == 200, res.data
    body = json.loads(res.data)
    row = body["rows"][0]
    assert row["requester_login"] == "requester"
    assert row["requester_hf_user_id"] == "u-requester"


# ---------------------------------------------------------------------------
# Intake: submit (new combination / new reciter) + accept / probe / resolve
# ---------------------------------------------------------------------------


def _links(n=114):
    return [{"chapter": c, "url": f"https://cdn.example/{c:03d}.mp3"} for c in range(1, n + 1)]


_ATTEST = {"distribution_rights": True, "links_verified": True, "storage_rights": True}


def _new_reciter_body(**over):
    body = {
        "kind": "new_reciter",
        "proposed_edits": {
            "name_en": "Test Reciter",
            "name_ar": "قارئ",
            "riwayah": "hafs",
            "style": "murattal",
        },
        "source": {"method": "links", "links": _links()},
        "comments": "From a clean studio master.",
        "attestations": dict(_ATTEST),
    }
    body.update(over)
    return body


def _new_combo_body(**over):
    body = {
        "kind": "existing_reciter_new_combo",
        "reciter_id": "rec_clean",
        "proposed_edits": {"riwayah": "warsh", "style": "murattal"},
        "source": {"method": "links", "links": _links()},
        "attestations": dict(_ATTEST),
    }
    body.update(over)
    return body


def test_intake_submit_anonymous_401(flask_client):
    res = flask_client.post(
        "/api/requests/intake", headers=_HEADERS, data=json.dumps(_new_reciter_body())
    )
    assert res.status_code == 401


def test_intake_submit_new_reciter_happy(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1", login="alice")
    res = client.post(
        "/api/requests/intake", headers=_HEADERS, data=json.dumps(_new_reciter_body())
    )
    assert res.status_code == 200, res.data
    body = json.loads(res.data)
    assert body["ok"] and body["id"].startswith("rq_")
    assert body["warnings"] == []

    # Appears in the admin open queue with intake shape (slugless, source).
    o_client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    rows = json.loads(o_client.get("/api/admin/requests?status=open").data)["rows"]
    intake = next(r for r in rows if r["kind"] == "new_reciter")
    assert intake["slug"] is None
    assert intake["name_en"] == "Test Reciter"
    assert intake["source"]["method"] == "links"
    assert len(intake["source"]["links"]) == 114


def test_list_requests_rows_carry_source_and_reciter_progress(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    playlist = "https://www.youtube.com/playlist?list=PLx"
    body = _new_combo_body(
        source={"method": "playlist", "playlist_url": playlist},
        attestations={**_ATTEST, "playlist_public": True},
    )
    res = client.post("/api/requests/intake", headers=_HEADERS, data=json.dumps(body))
    assert res.status_code == 200, res.data

    o_client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    rows = json.loads(o_client.get("/api/admin/requests?status=open").data)["rows"]
    edit = next(r for r in rows if r["slug"] == "rec_pending")
    assert (edit["channel"], edit["channel_name"], edit["source_kind"]) == (
        "ch1",
        "Channel One",
        "cdn",
    )
    assert (edit["reciter_published"], edit["reciter_requested"], edit["reciter_deliveries"]) == (
        0,
        1,
        1,
    )
    combo = next(r for r in rows if r["kind"] == "existing_reciter_new_combo")
    assert (combo["source_kind"], combo["source_url"], combo["link_count"]) == (
        "playlist",
        playlist,
        None,
    )
    assert (combo["reciter_published"], combo["reciter_requested"]) == (0, 0)


def test_intake_submit_missing_name_is_error(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    body = _new_reciter_body(proposed_edits={"riwayah": "hafs", "style": "murattal"})
    res = client.post("/api/requests/intake", headers=_HEADERS, data=json.dumps(body))
    assert res.status_code == 400
    assert any("English name" in e for e in json.loads(res.data)["errors"])


def test_intake_submit_missing_attestation_is_error(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    body = _new_reciter_body(
        attestations={"distribution_rights": True, "links_verified": False, "storage_rights": True}
    )
    res = client.post("/api/requests/intake", headers=_HEADERS, data=json.dumps(body))
    assert res.status_code == 400
    assert any("confirm all three" in e for e in json.loads(res.data)["errors"])


def test_intake_submit_missing_chapters_warns(signed_in_client):
    client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    body = _new_reciter_body(source={"method": "links", "links": _links(50)})
    res = client.post("/api/requests/intake", headers=_HEADERS, data=json.dumps(body))
    assert res.status_code == 200
    assert any("Missing" in w for w in json.loads(res.data)["warnings"])


def test_intake_discard_resolves_request(signed_in_client):
    c_client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    rid = json.loads(
        c_client.post(
            "/api/requests/intake", headers=_HEADERS, data=json.dumps(_new_combo_body())
        ).data
    )["id"]
    o_client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = o_client.post(
        f"/api/admin/requests/{rid}/discard",
        headers=_HEADERS,
        data=json.dumps({"reason": "Duplicate of an existing combo."}),
    )
    assert res.status_code == 200
    from services.db import repo_requests

    assert repo_requests.get_by_id(rid)["status"] == "discarded"


def test_intake_probe_caches_result(signed_in_client, monkeypatch):
    from qua_shared.schemas import ProbeResponse, ProbeResult
    from services.admin import intake as intake_service

    def _fake_probe(source):
        return ProbeResponse(
            at="2026-01-01T00:00:00+00:00",
            results=[ProbeResult(chapter=1, url="https://x/1.mp3", status=200, reachable=True)],
        )

    monkeypatch.setattr(intake_service, "probe_source", _fake_probe)

    c_client, _ = signed_in_client(role="contributor", hf_user_id="u-1")
    rid = json.loads(
        c_client.post(
            "/api/requests/intake", headers=_HEADERS, data=json.dumps(_new_combo_body())
        ).data
    )["id"]
    o_client, _ = signed_in_client(role="owner", hf_user_id="u-O")
    res = o_client.post(f"/api/admin/requests/{rid}/probe", headers=_HEADERS)
    assert res.status_code == 200
    assert json.loads(res.data)["results"][0]["reachable"] is True

    from services.db import _serde, repo_requests

    payload = _serde.json_loads(repo_requests.get_by_id(rid)["payload"])
    assert payload["probe"]["results"][0]["status"] == 200
