"""Wasl re-check — sidecar uids stay open until an answering op lands after
``_meta.created_at``.

The sidecar is read from disk through the real loader; effective edit-history
batches are stubbed at ``load_edit_history``.
"""

from __future__ import annotations

import json

import pytest

from services.validation import wasl_recheck as wr

SLUG = "recheck_reciter"
CREATED_AT = "2026-09-27T10:00:00Z"
AFTER = "2026-09-27T11:00:00.000Z"
BEFORE = "2026-09-26T09:00:00.000Z"


@pytest.fixture
def sidecar(tmp_path, tmp_reciter_dir):
    """Writes a two-uid ``wasl_recheck_v1.json`` for ``SLUG``; yields the uids."""
    d = tmp_path / "reciters" / SLUG
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "_meta": {"created_at": CREATED_AT, "reciter": SLUG, "kind": "wasl_recheck"},
        "by_uid": {
            "left-a": {"chapter": 2, "after_ref": "2:5:9", "reason": "pre-wasl split"},
            "left-b": {"chapter": 2, "after_ref": "2:6:4", "reason": "pre-wasl split"},
        },
    }
    (d / "wasl_recheck_v1.json").write_text(json.dumps(doc), encoding="utf-8")
    return ["left-a", "left-b"]


def _op(op_type: str, before: list[str], after: list[str]) -> dict:
    return {
        "op_type": op_type,
        "targets_before": [{"segment_uid": u} for u in before],
        "targets_after": [{"segment_uid": u} for u in after],
    }


def _stub_history(monkeypatch, batches: list[tuple[str, list[dict]]]) -> None:
    payload = {"batches": [{"saved_at_utc": at, "operations": ops} for at, ops in batches]}
    monkeypatch.setattr(wr, "load_edit_history", lambda _r: payload)


def test_absent_sidecar_yields_no_open_uids(tmp_reciter_dir, monkeypatch):
    _stub_history(monkeypatch, [])
    assert wr.open_wasl_recheck_uids(SLUG) == []


def test_unanswered_uids_are_open_in_sidecar_order(sidecar, monkeypatch):
    _stub_history(monkeypatch, [])
    assert wr.open_wasl_recheck_uids(SLUG) == ["left-a", "left-b"]


def test_set_is_wasl_after_created_at_closes_uid(sidecar, monkeypatch):
    _stub_history(monkeypatch, [(AFTER, [_op("set_is_wasl", ["left-a"], ["left-a"])])])
    assert wr.open_wasl_recheck_uids(SLUG) == ["left-b"]


def test_set_is_wasl_before_created_at_keeps_uid_open(sidecar, monkeypatch):
    _stub_history(monkeypatch, [(BEFORE, [_op("set_is_wasl", ["left-a"], ["left-a"])])])
    assert wr.open_wasl_recheck_uids(SLUG) == ["left-a", "left-b"]


def test_split_or_merge_touching_uid_after_created_at_closes_it(sidecar, monkeypatch):
    _stub_history(
        monkeypatch,
        [
            (AFTER, [_op("split_segment", ["left-a"], ["left-a", "new-1"])]),
            (AFTER, [_op("merge_segments", ["left-b", "right-b"], ["merged"])]),
        ],
    )
    assert wr.open_wasl_recheck_uids(SLUG) == []


def test_non_answering_op_after_created_at_keeps_uid_open(sidecar, monkeypatch):
    _stub_history(monkeypatch, [(AFTER, [_op("trim_segment", ["left-a"], ["left-a"])])])
    assert wr.open_wasl_recheck_uids(SLUG) == ["left-a", "left-b"]
