"""Claim / release / mark-ready / unmark-ready + reciter-task route tests.

The state-machine handlers in ``services/state.py`` already have their own
unit coverage; these tests exercise the HTTP boundary:

- happy paths return 200 with the new authoritative row
- one-claim-per-user policy returns 409 ``existing_claim``
- state-machine rejections surface as 400 / 403 / 404 via app errorhandlers
- predicates flip correctly across contributor / maintainer / anonymous
"""

from __future__ import annotations

import json

import pytest

# Helpers ---------------------------------------------------------------
# Mark-ready takes a JSON body. The route's pydantic gate accepts only the
# canonical six-key checklist (all True) plus two optional comment strings;
# the state handler additionally re-computes the live validation counts.
# Tests use this canonical payload + the ``clean_validation`` fixture to
# short-circuit the count gate so they don't need on-disk segs to pass.

_FULL_CHECKLIST_BODY = {
    "checklist": {
        "failed_alignments": True,
        "missing_words": True,
        "low_confidence": True,
        "repetitions": True,
        "splits_wasl_waqf": True,
        "basmala_amin_intros": True,
    },
    "comment_checks": "",
    "comment_issues": "",
}


@pytest.fixture
def clean_validation(monkeypatch):
    """Stub ``validate_reciter_segments`` to report all blocking counts at zero.

    Mark-ready re-validates segments live in the state handler; the test
    suite doesn't ship reciter segments, so we short-circuit to a clean
    response. Tests that need a non-zero count override this in-test.
    """
    from services import validation as _validation
    from services.state import state as _state

    def _ok(_reciter):
        return {
            "category_counts": {
                "low_confidence": 0,
                "low_confidence_v2": 0,
                "missed_waqf": 0,
                "cross_verse": 0,
                "basmala_amin": 0,
            },
            "checklist_keys": list(_FULL_CHECKLIST_BODY["checklist"]),
        }

    # The handler does ``from services.validation import …`` lazily inside
    # _h_marked_ready; that lookup goes through _validation.__dict__ every
    # call, so one ``setattr`` on the module covers it.
    monkeypatch.setattr(_validation, "validate_reciter_segments", _ok)
    return _ok


def _replace_state(rows: list):
    """Seed each spec from ``_row`` into the SQLite substrate (post-cutover the
    DB is the source of truth — the old in-memory ``_state_file`` is gone)."""
    from tests.conftest import _seed_state

    for spec in rows:
        _seed_state(**spec)


def _row(
    slug: str,
    *,
    state: str = "awaiting_review",
    assignee_hf_id: str | None = None,
    marked_ready: bool = False,
    visibility: str = "public",
):
    """Return a seed spec consumed by ``_replace_state`` → ``_seed_state``."""
    return dict(
        slug=slug,
        state=state,
        assignee_hf_id=assignee_hf_id,
        assignee_login="prev_owner" if assignee_hf_id else "test_user",
        marked_ready=marked_ready,
        visibility=visibility,
    )


# Claim/release/mark-ready tests use the `state_persistence` fixture
# (defined in tests/conftest.py) so state transitions persist through a real
# FilesystemBackend and exercise the actual write/read seam.


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def test_claim_anonymous_returns_401(flask_client, state_persistence):
    _replace_state([_row("test_slug", state="awaiting_review")])
    resp = flask_client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 401


def test_claim_happy_path(signed_in_client, state_persistence):
    _replace_state([_row("test_slug", state="awaiting_review")])

    client, user = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["state"] == "under_review"
    assert body["assignee_hf_id"] == "u-1"
    assert body["assignee_login"] == "alice"


def test_claim_persists_across_requests(signed_in_client, state_persistence):
    """Claim then a second request sees the new state without manual re-seed.

    Guards against regressions where _persist_row silently drops a write —
    the test relies on real persistence rather than a stub of the write seam.
    """
    _replace_state([_row("test_slug", state="awaiting_review")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp1 = client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp1.status_code == 200

    # Second request reads the post-claim state from _state_file.
    resp2 = client.get("/api/reciter-task/test_slug")
    assert resp2.status_code == 200
    body = json.loads(resp2.data)
    assert body["row"]["state"] == "under_review"
    assert body["row"]["assignee_hf_id"] == "u-1"
    # Predicates flipped correctly for the claimant.
    assert body["predicates"]["can_release"] is True
    assert body["predicates"]["can_claim"] is False  # already holds


def test_claim_one_claim_per_user_returns_409(signed_in_client, state_persistence):
    _replace_state(
        [
            _row("other", state="under_review", assignee_hf_id="u-1"),
            _row("target", state="awaiting_review"),
        ]
    )

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/claim/target",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 409
    body = json.loads(resp.data)
    assert body["existing_claim"] == "other"
    # The 409 body must carry display names so the toast can render
    # "Unclaim <name>..." rather than slug strings. When the test catalog
    # is empty (typical for unit tests) display_name returns None — the
    # field is present but None, and the frontend falls back to the raw
    # slug. See `inspector/services/catalog.display_name`.
    assert "existing_claim_name" in body
    assert "target_name" in body


def test_claim_marked_ready_does_not_block_new_claim(signed_in_client, state_persistence):
    """A contributor who has marked one reciter ready can claim another.

    The one-claim-per-user policy only blocks on UNMARKED open claims —
    marked-ready rows are admin-side until publish or send-back, so the
    reviewer is free to pick up something new while they wait."""
    _replace_state(
        [
            _row("marked", state="under_review", assignee_hf_id="u-1", marked_ready=True),
            _row("fresh", state="awaiting_review"),
        ]
    )

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/claim/fresh",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200, resp.data
    body = json.loads(resp.data)
    assert body["state"] == "under_review"
    assert body["assignee_hf_id"] == "u-1"


def test_can_claim_predicate_ignores_marked_ready_holdings(signed_in_client):
    """The ``can_claim`` predicate must mirror the policy — a user holding
    only marked-ready rows should see ``can_claim=True`` on an awaiting
    target (otherwise the FE would hide the Claim button after mark-ready
    and the new policy would never be exercised from the UI)."""
    _replace_state(
        [
            _row("marked", state="under_review", assignee_hf_id="u-1", marked_ready=True),
            _row("fresh", state="awaiting_review"),
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.get("/api/reciter-task/fresh")
    assert resp.status_code == 200
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_claim"] is True


def test_claim_discarded_returns_400(signed_in_client, state_persistence):
    _replace_state([_row("test_slug", state="awaiting_review", visibility="discarded")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 400


def test_claim_already_under_review_returns_400(signed_in_client, state_persistence):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other-user")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 400
    payload = json.loads(resp.data)
    assert payload["code"] == "STATE_PRECONDITION"
    assert payload["context"]["state_label"] == "under review"


def test_claim_missing_origin_returns_403(signed_in_client, state_persistence):
    """CSRF defense — POST without matching Origin/Referer is rejected."""
    _replace_state([_row("test_slug", state="awaiting_review")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post("/api/claim/test_slug")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def test_release_happy_path(signed_in_client, state_persistence):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/release/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["state"] == "awaiting_review"
    assert body["assignee_hf_id"] is None


def test_release_non_assignee_returns_403(signed_in_client, state_persistence):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/release/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 403


def test_release_maintainer_can_force_release(signed_in_client, state_persistence):
    """Maintainers can release someone else's claim through the normal
    ``/api/release`` route via the ``_require_claim_holder_or_maintainer`` gate.
    The dedicated owner-only ``/api/admin/claim/force-release`` endpoint with
    reason is covered in ``test_route_admin_actions.py``."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other")])

    client, _ = signed_in_client(hf_user_id="u-mod", login="mod", role="maintainer")
    resp = client.post(
        "/api/release/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# mark-ready / unmark-ready
# ---------------------------------------------------------------------------


def test_mark_unmark_round_trip(signed_in_client, state_persistence, clean_validation):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")

    resp1 = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    assert resp1.status_code == 200, resp1.data
    assert json.loads(resp1.data)["marked_ready"] is True

    # State persists across requests via the FilesystemBackend, so the
    # mark_ready=True flip from resp1 is already in _state_file when we hit
    # unmark-ready below — no manual reseed needed.

    resp2 = client.post(
        "/api/unmark-ready/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp2.status_code == 200
    assert json.loads(resp2.data)["marked_ready"] is False


def test_mark_ready_non_assignee_returns_403(
    signed_in_client,
    state_persistence,
    clean_validation,
):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    assert resp.status_code == 403


def test_mark_ready_rejects_unchecked_checklist(
    signed_in_client,
    state_persistence,
    clean_validation,
):
    """Submit without every checklist key checked -> 400 with details."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    body = {
        **_FULL_CHECKLIST_BODY,
        "checklist": {
            **_FULL_CHECKLIST_BODY["checklist"],
            "failed_alignments": False,
        },
    }
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=body,
    )
    assert resp.status_code == 400
    payload = json.loads(resp.data)
    assert payload["code"] == "MARK_READY_CHECKLIST"
    assert payload["details"]["unchecked"] == ["failed_alignments"]


def test_mark_ready_accepts_an_unchecked_box_whose_categories_never_had_items(
    signed_in_client,
    state_persistence,
    monkeypatch,
):
    """A box missing from ``checklist_keys`` is never shown, so it may be sent False."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    def _only_lc(_reciter):
        return {"category_counts": {}, "checklist_keys": ["low_confidence"]}

    from services import validation as _validation

    monkeypatch.setattr(_validation, "validate_reciter_segments", _only_lc)
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    body = {
        **_FULL_CHECKLIST_BODY,
        "checklist": {k: k == "low_confidence" for k in _FULL_CHECKLIST_BODY["checklist"]},
    }
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=body,
    )
    assert resp.status_code == 200, resp.data
    assert json.loads(resp.data)["marked_ready"] is True


def test_mark_ready_rejects_unlabelled_low_confidence_waqf(
    signed_in_client,
    state_persistence,
    monkeypatch,
):
    """An unlabelled Low Confidence Waqf cut blocks mark-ready."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    def _open_waqf(_reciter):
        return {
            "category_counts": {"missed_waqf": 2},
            "checklist_keys": list(_FULL_CHECKLIST_BODY["checklist"]),
        }

    from services import validation as _validation

    monkeypatch.setattr(_validation, "validate_reciter_segments", _open_waqf)
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    assert resp.status_code == 400
    assert json.loads(resp.data)["details"]["blocking_counts"] == {"missed_waqf": 2}


def test_mark_ready_rejects_nonzero_blocking_counts(
    signed_in_client,
    state_persistence,
    monkeypatch,
):
    """Submit when a blocking validation category is non-zero → 400 with details."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    def _has_lc(_reciter):
        # LC count must come from the items list (strict-threshold recompute),
        # not from ``category_counts.low_confidence`` directly — see
        # ``_h_marked_ready`` for the rationale. Three sub-0.80 items here.
        return {
            "low_confidence": [
                {"segment_uid": "u1", "confidence": 0.5, "chapter": 1, "seg_index": 0},
                {"segment_uid": "u2", "confidence": 0.6, "chapter": 1, "seg_index": 1},
                {"segment_uid": "u3", "confidence": 0.7, "chapter": 1, "seg_index": 2},
            ],
            "category_counts": {
                "low_confidence": 3,
                "low_confidence_v2": 0,
                "cross_verse": 0,
                "basmala_amin": 0,
                "repetitions": 0,
            },
        }

    from services import validation as _validation

    monkeypatch.setattr(_validation, "validate_reciter_segments", _has_lc)

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    assert resp.status_code == 400
    payload = json.loads(resp.data)
    assert payload["code"] == "MARK_READY_BLOCKING_COUNTS"
    assert payload["details"]["blocking_counts"] == {"low_confidence": 3}


def test_mark_ready_lc_count_uses_strict_threshold_not_detail(
    signed_in_client,
    state_persistence,
    monkeypatch,
):
    """Server gate must count low_confidence items at the strict 0.80
    cutoff (matching the FE accordion badge), NOT the detail-list size
    (which uses LOW_CONFIDENCE_DETAIL_THRESHOLD = 1.0 and includes every
    imperfect segment). Regression: server was rejecting reciters whose
    accordion + form both showed zero blocking items because
    ``category_counts.low_confidence`` reported the 0–100% detail count.
    """
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    # Validator response shaped exactly like real output: detail list
    # carries every imperfect segment (here three items at 0.85, 0.91, 0.99,
    # all ABOVE the 0.80 strict cutoff), category_counts mirrors len(detail).
    def _lots_of_detail(_reciter):
        return {
            "low_confidence": [
                {"segment_uid": "u1", "confidence": 0.85, "chapter": 1, "seg_index": 0},
                {"segment_uid": "u2", "confidence": 0.91, "chapter": 1, "seg_index": 1},
                {"segment_uid": "u3", "confidence": 0.99, "chapter": 1, "seg_index": 2},
            ],
            "category_counts": {
                "low_confidence": 3,
                "low_confidence_v2": 0,
                "cross_verse": 0,
                "basmala_amin": 0,
                "repetitions": 0,
            },
        }

    from services import validation as _validation

    monkeypatch.setattr(_validation, "validate_reciter_segments", _lots_of_detail)

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    # All three items are above 0.80 → strict count is 0 → gate passes.
    assert resp.status_code == 200, resp.data

    # Sanity: an item BELOW 0.80 still blocks.
    def _one_strict_lc(_reciter):
        return {
            "low_confidence": [
                {"segment_uid": "u1", "confidence": 0.79, "chapter": 1, "seg_index": 0},
                {"segment_uid": "u2", "confidence": 0.99, "chapter": 1, "seg_index": 1},
            ],
            "category_counts": {
                "low_confidence": 2,
                "low_confidence_v2": 0,
                "cross_verse": 0,
                "basmala_amin": 0,
                "repetitions": 0,
            },
        }

    monkeypatch.setattr(_validation, "validate_reciter_segments", _one_strict_lc)

    # Re-seed: previous request marked it ready, so flip back to under-review.
    _replace_state([_row("other_slug", state="under_review", assignee_hf_id="u-1")])
    resp = client.post(
        "/api/mark-ready/other_slug",
        headers={"Origin": "http://localhost"},
        json=_FULL_CHECKLIST_BODY,
    )
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert body["details"]["blocking_counts"] == {"low_confidence": 1}


def test_mark_ready_rejects_malformed_body(
    signed_in_client,
    state_persistence,
    clean_validation,
):
    """Missing checklist field → 400 from the route-level pydantic gate."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json={"comment_checks": "without checklist"},
    )
    assert resp.status_code == 400
    payload = json.loads(resp.data)
    assert payload["code"] == "MARK_READY_PAYLOAD"
    assert "validation_errors" in payload["details"]


def test_release_blocked_after_marked_ready(
    signed_in_client,
    state_persistence,
    clean_validation,
):
    """Self-release on a marked-ready row must refuse — the reviewer
    has to unmark first (or an admin force-releases)."""
    _replace_state(
        [
            _row("test_slug", state="under_review", assignee_hf_id="u-1", marked_ready=True),
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/release/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 400
    assert json.loads(resp.data)["code"] == "RELEASE_BLOCKED_MARKED_READY"


# ---------------------------------------------------------------------------
# owner mark-ready bypass (claim.mark_ready_skip_gates)
# ---------------------------------------------------------------------------


def test_mark_ready_owner_bypass_accepts_empty_body(signed_in_client, state_persistence):
    """Owners hold ``claim.mark_ready_skip_gates`` by default — POST with no
    body must succeed even when the validation segs would normally block.
    The handler skips the count gate AND the Pydantic validation when the
    bypass capability is held."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-owner")])

    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_u", role="owner")
    # NOTE: deliberately don't mock validate_reciter_segments — the bypass
    # path should never call it. If the handler dropped the bypass branch
    # this test would fail with "no segments found on bucket".
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json={},
    )
    assert resp.status_code == 200, resp.data
    assert json.loads(resp.data)["marked_ready"] is True


def test_mark_ready_owner_bypass_persists_bypass_used(signed_in_client, state_persistence):
    """The handler must stamp ``bypass_used=1`` on the open claim so the
    admin Reviews drawer can render the bypass pill."""
    from services.db import repo_claims

    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-owner")])
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_u", role="owner")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json={},
    )
    assert resp.status_code == 200, resp.data

    open_claim = repo_claims.get_open_claim("test_slug")
    assert open_claim is not None
    assert open_claim["mark_ready_bypass_used"] == 1
    # No checklist persisted on a bypass submission.
    assert open_claim["mark_ready_checklist"] is None


def test_mark_ready_non_owner_still_requires_form_body(
    signed_in_client,
    state_persistence,
    clean_validation,
):
    """A contributor without the override capability still hits the
    Pydantic gate — empty body → 400. Sanity check that the bypass path
    isn't accidentally exposed."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json={},
    )
    assert resp.status_code == 400
    payload = json.loads(resp.data)
    assert payload["code"] == "MARK_READY_PAYLOAD"


def test_mark_ready_owner_bypass_predicate_surfaces(signed_in_client, state_persistence):
    """``can_skip_mark_ready_gates`` must be true for owners holding a claim,
    false for the contributor assignee, and false for any non-claim-holder."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-owner")])
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_u", role="owner")
    resp = client.get("/api/reciter-task/test_slug")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_mark_ready"] is True
    assert preds["can_skip_mark_ready_gates"] is True

    # Re-seed with a contributor claim-holder. Same role test should
    # surface bypass=False.
    _replace_state([_row("contrib_slug", state="under_review", assignee_hf_id="u-2")])
    client2, _ = signed_in_client(hf_user_id="u-2", login="bob")
    resp = client2.get("/api/reciter-task/contrib_slug")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_mark_ready"] is True
    assert preds["can_skip_mark_ready_gates"] is False


# ---------------------------------------------------------------------------
# reciter-task
# ---------------------------------------------------------------------------


def test_reciter_task_404_for_unknown_slug(flask_client):
    _replace_state([])
    resp = flask_client.get("/api/reciter-task/nope")
    assert resp.status_code == 404


def test_reciter_task_predicates_anonymous(flask_client):
    _replace_state([_row("test_slug", state="awaiting_review")])
    resp = flask_client.get("/api/reciter-task/test_slug")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    preds = body["predicates"]
    # Anonymous → every contribution predicate is False
    assert preds == {
        "can_claim": False,
        "can_edit": False,
        "can_edit_as_admin": False,
        "can_edit_as_owner": False,
        "can_mark_ready": False,
        "can_skip_mark_ready_gates": False,
        "can_unmark_ready": False,
        "can_release": False,
    }


def test_reciter_task_predicates_assignee(signed_in_client):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="u-1")])
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.get("/api/reciter-task/test_slug")
    body = json.loads(resp.data)
    preds = body["predicates"]
    assert preds["can_edit"] is True
    assert preds["can_mark_ready"] is True
    assert preds["can_release"] is True
    assert preds["can_claim"] is False  # already holds; not awaiting_review


def test_reciter_task_predicates_maintainer_can_edit_as_admin(signed_in_client):
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other")])
    client, _ = signed_in_client(hf_user_id="u-mod", login="mod", role="maintainer")
    resp = client.get("/api/reciter-task/test_slug")
    body = json.loads(resp.data)
    preds = body["predicates"]
    assert preds["can_edit"] is False  # not assignee
    assert preds["can_edit_as_admin"] is True
    assert preds["can_release"] is False  # not assignee on plain release
    # Mark-ready/unmark-ready remain gated to the assignee.
    assert preds["can_mark_ready"] is False


def test_reciter_task_predicates_one_claim_per_user(signed_in_client):
    _replace_state(
        [
            _row("other", state="under_review", assignee_hf_id="u-1"),
            _row("target", state="awaiting_review"),
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.get("/api/reciter-task/target")
    body = json.loads(resp.data)
    assert body["predicates"]["can_claim"] is False  # has_other_active_claim


def test_reciter_task_predicates_marked_ready_frozen(signed_in_client):
    _replace_state(
        [
            _row(
                "test_slug",
                state="under_review",
                assignee_hf_id="u-1",
                marked_ready=True,
            )
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.get("/api/reciter-task/test_slug")
    preds = json.loads(resp.data)["predicates"]
    # Marked-ready means the row is frozen for the reviewer; can_edit /
    # can_mark_ready / can_release are all False. Only ``can_unmark_ready``
    # remains as the thaw affordance — but the segments footer doesn't
    # currently surface it; admins drive forward/sendback from the Reviews
    # drawer.
    assert preds["can_edit"] is False
    assert preds["can_mark_ready"] is False
    assert preds["can_unmark_ready"] is True
    assert preds["can_release"] is False


# ---------------------------------------------------------------------------
# Owner-only permissions
# ---------------------------------------------------------------------------


def test_owner_can_claim_multiple_reciters(signed_in_client, state_persistence):
    """Owner bypasses the one-claim-per-user policy."""
    _replace_state(
        [
            _row("already_claimed", state="under_review", assignee_hf_id="u-owner"),
            _row("second_target", state="awaiting_review"),
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_user", role="owner")
    resp = client.post(
        "/api/claim/second_target",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["state"] == "under_review"
    assert body["assignee_hf_id"] == "u-owner"


def test_reciter_task_predicates_owner_can_edit_as_owner(signed_in_client):
    """Owner gets can_edit_as_owner=True on any public row (any state)."""
    # Test with a catalogued row (state that normally blocks all edits)
    _replace_state([_row("test_slug", state="catalogued")])
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_user", role="owner")
    resp = client.get("/api/reciter-task/test_slug")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_edit_as_owner"] is True
    assert preds["can_edit"] is False  # not assignee
    assert preds["can_edit_as_admin"] is False  # not under_review


def test_reciter_task_predicates_owner_can_claim_with_other_active(signed_in_client):
    """Owner's can_claim is True even when they already hold another claim."""
    _replace_state(
        [
            _row("held", state="under_review", assignee_hf_id="u-owner"),
            _row("target", state="awaiting_review"),
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_user", role="owner")
    resp = client.get("/api/reciter-task/target")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_claim"] is True


def test_reciter_task_predicates_owner_marked_ready_overrides(signed_in_client):
    """Owner can still edit a marked_ready row — owner override is total."""
    _replace_state(
        [
            _row(
                "test_slug",
                state="under_review",
                assignee_hf_id="other",
                marked_ready=True,
            )
        ]
    )
    client, _ = signed_in_client(hf_user_id="u-owner", login="owner_user", role="owner")
    resp = client.get("/api/reciter-task/test_slug")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_edit_as_owner"] is True


def test_reciter_task_predicates_non_owner_lacks_can_edit_as_owner(signed_in_client):
    """Maintainer does not get can_edit_as_owner even on under_review rows."""
    _replace_state([_row("test_slug", state="under_review", assignee_hf_id="other")])
    client, _ = signed_in_client(hf_user_id="u-mod", login="mod", role="maintainer")
    resp = client.get("/api/reciter-task/test_slug")
    preds = json.loads(resp.data)["predicates"]
    assert preds["can_edit_as_owner"] is False
    assert preds["can_edit_as_admin"] is True  # still gets admin edit
