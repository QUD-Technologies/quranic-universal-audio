"""Coverage for ``services.admin.reviews.get_review_detail`` — the per-slug
payload behind the admin Reviews General drawer (``GET /api/admin/reviews/<slug>``).

This path had ZERO coverage, which is how the missing ``mark_ready_*`` columns
(see ``test_migration_0007``) shipped a 500 on every detail fetch. These tests
exercise the under-review and marked-ready shapes the drawer actually opens on.
"""

from __future__ import annotations

from services import db
from services.admin import reviews as reviews_service
from services.db import _serde, repo_claims


def test_detail_unknown_slug_returns_none():
    assert reviews_service.get_review_detail("nope") is None


def test_detail_under_review_open_claim_no_submission(seed_state):
    """The user's exact scenario: an ``under_review`` row with an open claim
    that has NOT been marked ready. Must return a payload (not raise) with the
    current claim present and no mark-ready submission."""
    seed_state(
        "d_ur",
        state="under_review",
        assignee_hf_id="rev1",
        assignee_login="reviewer1",
    )

    detail = reviews_service.get_review_detail("d_ur")

    assert detail is not None
    assert detail["state"] == "under_review"
    assert detail["current_claim"] is not None
    assert detail["current_claim"]["login"] == "reviewer1"
    assert detail["current_claim"]["marked_ready_at"] is None
    assert detail["current_claim"]["mark_ready_submission"] is None


def test_detail_marked_ready_round_trips_submission(seed_state):
    """A marked-ready claim with a full checklist must round-trip through
    ``_build_submission`` into the wire shape."""
    seed_state(
        "d_mr",
        state="under_review",
        assignee_hf_id="rev2",
        assignee_login="reviewer2",
    )
    checklist = {
        "failed_alignments": True,
        "missing_words": True,
        "low_confidence": True,
        "repetitions": True,
        "splits_wasl_waqf": True,
    }
    with db.transaction():
        repo_claims.set_marked_ready(
            "d_mr",
            ready=True,
            checklist_json=_serde.json_dumps(checklist),
            comment_checks="listened end to end",
            comment_issues="none",
        )

    detail = reviews_service.get_review_detail("d_mr")

    assert detail is not None
    sub = detail["current_claim"]["mark_ready_submission"]
    assert sub is not None
    assert sub["checklist"] == checklist
    assert sub["comment_checks"] == "listened end to end"
    assert sub["comment_issues"] == "none"


def test_detail_legacy_marked_ready_without_checklist(seed_state):
    """A legacy claim marked ready before the checklist form existed has
    ``marked_ready_at`` set but no checklist JSON — ``_build_submission`` must
    yield None rather than raising."""
    seed_state(
        "d_legacy",
        state="under_review",
        assignee_hf_id="rev3",
        assignee_login="reviewer3",
        marked_ready=True,  # set_marked_ready with no checklist_json
    )

    detail = reviews_service.get_review_detail("d_legacy")

    assert detail is not None
    assert detail["current_claim"]["marked_ready_at"] is not None
    assert detail["current_claim"]["mark_ready_submission"] is None


def test_detail_drops_retired_checklist_key_from_old_claim(seed_state):
    """Claims marked ready before the ``basmala_amin`` category was retired
    store ``basmala_amin_intros`` in their checklist JSON. ``MarkReadyChecklist``
    forbids unknown keys, so the drawer must drop it rather than 500."""
    seed_state(
        "d_old",
        state="under_review",
        assignee_hf_id="rev4",
        assignee_login="reviewer4",
    )
    current = {
        "failed_alignments": True,
        "missing_words": False,
        "low_confidence": True,
        "repetitions": False,
        "splits_wasl_waqf": True,
    }
    with db.transaction():
        repo_claims.set_marked_ready(
            "d_old",
            ready=True,
            checklist_json=_serde.json_dumps({**current, "basmala_amin_intros": True}),
        )

    detail = reviews_service.get_review_detail("d_old")

    assert detail is not None
    assert detail["current_claim"]["mark_ready_submission"]["checklist"] == current
