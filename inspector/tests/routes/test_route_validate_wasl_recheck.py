"""``GET /api/seg/validate`` — ``wasl_recheck`` lists open sidecar uids for
every viewer, and a saved same-value ``set_is_wasl`` answer closes the uid."""

from __future__ import annotations

import json

import pytest

from services.storage.data_loader import load_detailed

RECITER = "fixture_reciter"
CHAPTER = 112
_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


@pytest.fixture
def recheck_uid(tmp_path, tmp_reciter_dir):
    """Installs 112-ikhlas under review for ``test-user-1`` with a recheck
    sidecar naming the first segment; yields that uid."""
    tmp_reciter_dir.install(RECITER, "112-ikhlas", under_review_for="test-user-1")
    uid = load_detailed(RECITER)[0]["segments"][0]["segment_uid"]
    doc = {
        "_meta": {"created_at": "2026-01-01T00:00:00Z", "reciter": RECITER, "kind": "wasl_recheck"},
        "by_uid": {uid: {"chapter": CHAPTER, "after_ref": "112:1:4", "reason": "pre-wasl split"}},
    }
    (tmp_path / "reciters" / RECITER / "wasl_recheck_v1.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )
    return uid


def _validate(client) -> dict:
    res = client.get(f"/api/seg/validate/{RECITER}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def _waqf_answer(seg: dict) -> dict:
    """A ``set_is_wasl`` op answering WAQF on a seg already ``is_wasl: false``."""
    snap = {"segment_uid": seg["segment_uid"], "matched_ref": seg["matched_ref"], "is_wasl": False}
    return {
        "op_id": "op-recheck-1",
        "op_type": "set_is_wasl",
        "type": "setIsWasl",
        "command": {"type": "setIsWasl", "segmentUid": seg["segment_uid"], "is_wasl": False},
        "fix_kind": "manual",
        "targets_before": [snap],
        "targets_after": [snap],
    }


def test_anonymous_viewer_sees_open_recheck_uid(flask_client, recheck_uid):
    assert _validate(flask_client)["wasl_recheck"] == [recheck_uid]


def test_same_value_answer_saves_and_closes_uid(signed_in_client, recheck_uid):
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")
    segs = load_detailed(RECITER)[0]["segments"]
    assert segs[0].get("is_wasl", False) is False
    patch = {
        "segments": [{"index": 0, "matched_ref": segs[0]["matched_ref"]}],
        "operations": [_waqf_answer(segs[0])],
    }
    res = client.post(
        f"/api/seg/save/{RECITER}/{CHAPTER}", data=json.dumps(patch), headers=_HEADERS
    )
    assert res.status_code == 200, res.get_json()
    assert _validate(client)["wasl_recheck"] == []
