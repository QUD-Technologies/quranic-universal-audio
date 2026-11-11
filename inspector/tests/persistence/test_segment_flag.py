"""Segment flag thread — schema round-trip + save-flow apply/unflag.

The flag rides the normal command/save pipeline (op_type ``flag_segment``).
The actor + timestamps are stamped server-side; the persisted shape is the
canonical ``SegmentFlag`` (``actor`` / ``flagged_at_utc`` / ``follow_ups``),
never the FE-redacted display shape.
"""

from __future__ import annotations

import json

from qua_shared.schemas.bucket.segment import SegmentFlag, parse_detailed_segment

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


def test_flagged_segment_round_trips_byte_equal():
    """A seg carrying a flag (with a follow-up) survives parse → dump unchanged."""
    seg = {
        "segment_uid": "uid-1",
        "time_start": 0,
        "time_end": 1000,
        "matched_ref": "112:1:1-112:1:4",
        "confidence": 1.0,
        "flag": {
            "comment": "weird tail",
            "actor": {"hf_user_id": "u1", "login_at_time": "alice", "role": "contributor"},
            "flagged_at_utc": "2026-06-06T00:00:00.000Z",
            "follow_ups": [
                {
                    "comment": "agreed",
                    "actor": {"hf_user_id": "u2", "login_at_time": "bob", "role": "maintainer"},
                    "at_utc": "2026-06-06T00:01:00.000Z",
                }
            ],
        },
    }
    parsed = parse_detailed_segment(seg)
    dumped = parsed.model_dump(exclude_none=True)
    assert dumped["flag"] == seg["flag"]


def _flag_op(uid: str, comment: str, intent: str) -> dict:
    return {
        "op_id": f"op-{intent}-{uid}",
        "type": "flagSegment",
        "op_type": "flag_segment",
        "op_context_category": None,
        "fix_kind": "manual",
        "targets_before": [],
        "targets_after": [],
        "command": {
            "type": "flagSegment",
            "segmentUid": uid,
            "comment": comment,
            "intent": intent,
        },
    }


def _save(client, reciter, chapter, uid, comment, intent):
    payload = {
        "segments": [{"index": 0, "segment_uid": uid, "matched_ref": "", "confidence": 1.0}],
        "operations": [_flag_op(uid, comment, intent)],
    }
    return client.post(
        f"/api/seg/save/{reciter}/{chapter}",
        data=json.dumps(payload),
        headers=_HEADERS,
    )


def _first_seg_on_disk(tmp_reciter_dir, reciter) -> dict:
    saved = json.loads(
        (tmp_reciter_dir.root / reciter / "detailed.json").read_text(encoding="utf-8")
    )
    return saved["entries"][0]["segments"][0]


def test_flag_save_persists_canonical_actor(load_fixture, tmp_reciter_dir, signed_in_client):
    """Setting a flag stamps the server actor and the canonical persisted shape."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")
    uid = load_fixture("112-ikhlas")["entries"][0]["segments"][0]["segment_uid"]

    res = _save(client, reciter, 112, uid, "double-check this boundary", "set")
    assert res.status_code == 200

    flag = _first_seg_on_disk(tmp_reciter_dir, reciter)["flag"]
    assert flag["comment"] == "double-check this boundary"
    assert flag["actor"]["hf_user_id"] == "test-user-1"
    assert flag["follow_ups"] == []
    # Canonical shape — never the FE-redacted display shape.
    assert "author" not in flag and "mine" not in flag
    SegmentFlag.model_validate(flag)  # validates fully


def test_edit_empty_comment_unflags(load_fixture, tmp_reciter_dir, signed_in_client):
    """Clearing the root comment removes the whole flag (the unflag mechanism)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")
    uid = load_fixture("112-ikhlas")["entries"][0]["segments"][0]["segment_uid"]

    assert _save(client, reciter, 112, uid, "needs a look", "set").status_code == 200
    assert "flag" in _first_seg_on_disk(tmp_reciter_dir, reciter)

    assert _save(client, reciter, 112, uid, "", "edit").status_code == 200
    assert "flag" not in _first_seg_on_disk(tmp_reciter_dir, reciter)


def test_non_flagger_cannot_edit_comment(load_fixture, tmp_reciter_dir, signed_in_client):
    """Only the original flagger may edit the root comment."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")
    uid = load_fixture("112-ikhlas")["entries"][0]["segments"][0]["segment_uid"]
    assert _save(client, reciter, 112, uid, "alice's note", "set").status_code == 200

    # A different user (also a reviewer of this reciter) tries to edit the root.
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-2")
    other, _ = signed_in_client(hf_user_id="test-user-2", login="bob")
    res = _save(other, reciter, 112, uid, "bob's overwrite", "edit")
    assert res.status_code == 403


def test_owner_can_delete_any_flag(load_fixture, tmp_reciter_dir, signed_in_client):
    """``delete`` removes someone else's whole flag thread for a ``segments.delete_flag``
    holder (the owner by default)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")
    uid = load_fixture("112-ikhlas")["entries"][0]["segments"][0]["segment_uid"]
    assert _save(client, reciter, 112, uid, "alice's note", "set").status_code == 200

    owner, _ = signed_in_client(hf_user_id="owner-1", login="owen", role="owner")
    assert _save(owner, reciter, 112, uid, "", "delete").status_code == 200
    assert "flag" not in _first_seg_on_disk(tmp_reciter_dir, reciter)


def test_maintainer_cannot_delete_flag_by_default(load_fixture, tmp_reciter_dir, signed_in_client):
    """``segments.delete_flag`` is owner-only by default — a maintainer gets 403."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="maint-1")
    client, _ = signed_in_client(hf_user_id="maint-1", login="mia", role="maintainer")
    uid = load_fixture("112-ikhlas")["entries"][0]["segments"][0]["segment_uid"]
    assert _save(client, reciter, 112, uid, "a note", "set").status_code == 200

    assert _save(client, reciter, 112, uid, "", "delete").status_code == 403
    assert "flag" in _first_seg_on_disk(tmp_reciter_dir, reciter)
