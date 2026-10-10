"""Cross-surface state parity tests.

After a state mutation (claim / release / mark-ready), the public list
endpoint, the public detail endpoint, and the reciter-task endpoint must
all agree on the row's state, assignee, and primary bucket. Each surface
was previously tested in isolation; this file pins them together so a
divergence between them (e.g. one cached, one fresh) surfaces as a test
failure rather than as a user-visible UX bug on the deployed Space.

These tests use the real ``state_persistence`` fixture (filesystem backend
under tmp_path) — no `_stub_persist` mock — so mutations roundtrip through
``_persist_row`` and the next GET reads the persisted state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone


def _state_row_kwargs(
    slug: str,
    *,
    state: str = "awaiting_review",
    marked_ready: bool = False,
    assignee_hf_id: str | None = None,
    visibility: str = "public",
):
    from qua_shared.schemas import ReciterRow, ReciterState, Visibility

    return ReciterRow(
        slug=slug,
        state=ReciterState(state),
        state_since=datetime(2026, 5, 12, tzinfo=UTC),
        assignee_hf_id=assignee_hf_id,
        assignee_login="prev_owner" if assignee_hf_id else None,
        assignee_since=datetime(2026, 5, 12, tzinfo=UTC) if assignee_hf_id else None,
        marked_ready=marked_ready,
        visibility=Visibility(visibility),
    )


def _seed_state(rows: list):
    """Seed each ReciterRow into the SQLite substrate (call ``_seed_catalog``
    first so the delivery FK target exists)."""
    from tests.conftest import _seed_state as _seed

    for r in rows:
        _seed(
            r.slug,
            state=r.state.value,
            state_since=r.state_since,
            visibility=r.visibility.value,
            visibility_reason=r.visibility_reason,
            assignee_hf_id=r.assignee_hf_id,
            assignee_login=r.assignee_login or "test_user",
            marked_ready=r.marked_ready,
        )


def _seed_catalog(
    slug: str, *, reciter_id: str = "test_reciter", name_en: str = "Test Reciter"
) -> None:
    """Install a minimal valid catalog containing one reciter + one delivery
    keyed off `slug`. The vocab carries just enough rows to satisfy FK
    validation on the Delivery model."""
    from qua_shared.schemas import (
        AudioCategory,
        Channel,
        Delivery,
        ReciterCatalog,
        ReciterEntry,
        RecordingContext,
        Riwayah,
        Source,
        Style,
        Vocab,
    )
    from services import catalog as catalog_service

    vocab = Vocab(
        riwayat=[Riwayah(slug="hafs", short="hafs", name="Hafs")],
        styles=[Style(slug="murattal", short="murattal", name="Murattal")],
        sources=[
            Source(slug="mp3quran", name="MP3Quran", audio_categories=[AudioCategory.BY_SURAH])
        ],
        channels=[Channel(slug="mp3quran", short="mp3q", name="MP3Quran")],
        recording_contexts=[RecordingContext(slug="studio", name="Studio")],
    )
    reciter = ReciterEntry(
        reciter_id=reciter_id,
        name_en=name_en,
        country="Saudi Arabia",
    )
    delivery = Delivery(
        slug=slug,
        reciter_id=reciter_id,
        riwayah="hafs",
        style="murattal",
        recording_context="studio",
        source="mp3quran",
        channel="mp3quran",
        audio_category=AudioCategory.BY_SURAH,
        chapter_count=114,
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        added_by_hf_id="system_seed",
    )
    from tests.conftest import _seed_catalog as _seedcat

    _seedcat(vocab=vocab, reciters=[reciter], deliveries=[delivery])


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------


def test_claim_visible_in_public_reciters_and_detail_and_reciter_task(
    signed_in_client,
    state_persistence,
):
    """After claim, /api/public/reciters AND /api/public/reciter/<id> AND
    /api/reciter-task/<slug> all agree on the new state + assignee."""
    _seed_catalog("test_slug", reciter_id="test_reciter", name_en="Test Reciter")
    _seed_state([_state_row_kwargs("test_slug", state="awaiting_review")])

    # Claim as maintainer (admin shape on detail surfaces the assignee).
    client, _ = signed_in_client(hf_user_id="u-mod", login="mod", role="maintainer")
    resp = client.post(
        "/api/claim/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200, resp.data

    # Surface 1: list endpoint -- primary_bucket should be under_review
    resp_list = client.get("/api/public/reciters")
    assert resp_list.status_code == 200
    list_body = json.loads(resp_list.data)
    matched = [r for r in list_body["reciters"] if r["reciter_id"] == "test_reciter"]
    assert matched, "test_reciter missing from /api/public/reciters"
    assert matched[0]["primary_bucket"] == "under_review"

    # Surface 2: detail endpoint reports the same bucket.
    # Note: assignee_hf_id is intentionally NOT in PublicDelivery — the
    # public + admin detail payloads both redact it. Assignee is surfaced
    # only via /api/reciter-task (next assertion).
    resp_detail = client.get("/api/public/reciter/test_reciter")
    assert resp_detail.status_code == 200
    detail_body = json.loads(resp_detail.data)
    assert detail_body["primary_bucket"] == "under_review"
    assert any(d["bucket"] == "under_review" for d in detail_body["deliveries"])

    # Surface 3: reciter-task endpoint reports the same row (this one DOES
    # carry assignee_hf_id; the user owning the claim must see themselves).
    resp_task = client.get("/api/reciter-task/test_slug")
    assert resp_task.status_code == 200
    task_body = json.loads(resp_task.data)
    assert task_body["row"]["state"] == "under_review"
    assert task_body["row"]["assignee_hf_id"] == "u-mod"


def test_release_propagates_to_all_endpoints(signed_in_client, state_persistence):
    """Release returns the row to awaiting_review across every surface."""
    _seed_catalog("test_slug")
    _seed_state([_state_row_kwargs("test_slug", state="under_review", assignee_hf_id="u-1")])

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/release/test_slug",
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code == 200, resp.data

    # List + detail + task all see awaiting_review with no assignee.
    list_body = json.loads(client.get("/api/public/reciters").data)
    matched = [r for r in list_body["reciters"] if r["reciter_id"] == "test_reciter"]
    assert matched and matched[0]["primary_bucket"] == "available_for_review"

    detail_body = json.loads(client.get("/api/public/reciter/test_reciter").data)
    assert detail_body["primary_bucket"] == "available_for_review"
    assert all(d.get("assignee_hf_id") is None for d in detail_body["deliveries"])

    task_body = json.loads(client.get("/api/reciter-task/test_slug").data)
    assert task_body["row"]["state"] == "awaiting_review"
    assert task_body["row"]["assignee_hf_id"] is None


def test_mark_ready_propagates_to_all_endpoints(
    signed_in_client,
    state_persistence,
    monkeypatch,
):
    """Mark-ready persists the marked_ready flag; bucket stays under_review."""
    _seed_catalog("test_slug")
    _seed_state([_state_row_kwargs("test_slug", state="under_review", assignee_hf_id="u-1")])

    # The mark-ready state handler re-validates the live segments to gate
    # on five category_counts being zero. Tests don't ship segments — stub
    # the validator to report a clean result so the gate passes.
    from services import validation as _validation

    def _ok(_reciter):
        return {
            "category_counts": {
                "low_confidence": 0,
                "low_confidence_v2": 0,
                "cross_verse": 0,
                "basmala_amin": 0,
                "repetitions": 0,
            }
        }

    monkeypatch.setattr(_validation, "validate_reciter_segments", _ok)

    client, _ = signed_in_client(hf_user_id="u-1", login="alice")
    resp = client.post(
        "/api/mark-ready/test_slug",
        headers={"Origin": "http://localhost"},
        json={
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
        },
    )
    assert resp.status_code == 200, resp.data

    list_body = json.loads(client.get("/api/public/reciters").data)
    matched = [r for r in list_body["reciters"] if r["reciter_id"] == "test_reciter"]
    # marked_ready stays internal -- public bucket remains under_review.
    assert matched and matched[0]["primary_bucket"] == "under_review"

    task_body = json.loads(client.get("/api/reciter-task/test_slug").data)
    assert task_body["row"]["state"] == "under_review"
    assert task_body["row"]["marked_ready"] is True
    # marked_ready freezes can_edit / can_mark_ready; only can_unmark_ready stays True.
    assert task_body["predicates"]["can_unmark_ready"] is True
    assert task_body["predicates"]["can_mark_ready"] is False


def test_anonymous_sees_redacted_assignee_consistently(
    state_persistence,
    flask_client,
    signed_in_client,
):
    """Anonymous callers must NOT see the assignee on either endpoint.

    Asserting "assignee_hf_id not in delivery" is vacuous because the public
    mapper never adds the field under any condition (redaction-by-omission).
    Instead drive the same routes anonymously AND signed in as a maintainer,
    then assert the deliveries shape is structurally equivalent — a future
    leak that flipped the public mapper to add the field for authenticated
    callers would surface here.
    """
    _seed_catalog("test_slug")
    _seed_state([_state_row_kwargs("test_slug", state="under_review", assignee_hf_id="u-1")])

    # Anonymous (no session cookie) — uses flask_client directly.
    anon_list = json.loads(flask_client.get("/api/public/reciters").data)
    anon_match = [r for r in anon_list["reciters"] if r["reciter_id"] == "test_reciter"]
    assert anon_match
    anon_delivery_keys = {tuple(sorted(d.keys())) for d in anon_match[0]["deliveries"]}

    # Maintainer hits the same anonymous-equivalent public route.
    client, _ = signed_in_client(role="maintainer", hf_user_id="u-M")
    auth_list = json.loads(client.get("/api/public/reciters").data)
    auth_match = [r for r in auth_list["reciters"] if r["reciter_id"] == "test_reciter"]
    assert auth_match
    auth_delivery_keys = {tuple(sorted(d.keys())) for d in auth_match[0]["deliveries"]}

    assert anon_delivery_keys == auth_delivery_keys, (
        "public route leaked extra fields to authenticated caller"
    )

    detail_body = json.loads(flask_client.get("/api/public/reciter/test_reciter").data)
    for d in detail_body["deliveries"]:
        assert "assignee_hf_id" not in d, "detail endpoint leaked assignee to anonymous caller"
