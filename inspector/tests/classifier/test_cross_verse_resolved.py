"""Resolved cross-verse items — split roots recovered from edit history.

The builder reads effective batches via ``load_edit_history`` (stubbed here),
picks every ``split_segment`` op launched from the cross-verse card or whose
parent ref spanned verses, and emits a ``resolved: True`` item for each root
that is still live and single-verse.
"""

from __future__ import annotations

from services.validation import cross_verse_resolved as cvr


def _split_op(root_uid: str, parent_ref: str, ctx: str | None = "cross_verse") -> dict:
    return {
        "op_type": "split_segment",
        "op_context_category": ctx,
        "targets_before": [{"segment_uid": root_uid, "matched_ref": parent_ref}],
        "targets_after": [
            {"segment_uid": root_uid, "matched_ref": "2:1:1-2:1:1"},
            {"segment_uid": f"{root_uid}-b", "matched_ref": "2:2:1-2:2:5"},
        ],
    }


def _entries(*segs: tuple[str, str]) -> list[dict]:
    return [
        {
            "ref": "2",
            "segments": [{"segment_uid": uid, "matched_ref": ref} for uid, ref in segs],
        }
    ]


def _stub_history(monkeypatch, ops: list[dict]) -> None:
    monkeypatch.setattr(cvr, "load_edit_history", lambda _r: {"batches": [{"operations": ops}]})


def test_root_from_card_split_is_emitted_resolved(monkeypatch):
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:2:5")])
    items = cvr.resolved_cross_verse_items(
        "slug", _entries(("root", "2:1:1-2:1:1"), ("root-b", "2:2:1-2:2:5")), set()
    )
    assert items == [
        {
            "chapter": 2,
            "seg_index": 0,
            "segment_uid": "root",
            "ref": "2:1:1-2:1:1",
            "classified_issues": [],
            "resolved": True,
        }
    ]


def test_pre_card_split_qualifies_by_parent_ref(monkeypatch):
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:2:5", ctx=None)])
    items = cvr.resolved_cross_verse_items("slug", _entries(("root", "2:1:1-2:1:1")), set())
    assert [i["segment_uid"] for i in items] == ["root"]


def test_single_verse_split_without_context_is_ignored(monkeypatch):
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:1:5", ctx=None)])
    assert cvr.resolved_cross_verse_items("slug", _entries(("root", "2:1:1-2:1:2")), set()) == []


def test_root_still_cross_verse_is_left_to_live_pass(monkeypatch):
    """An undone split leaves the root compound again — the live pass owns it."""
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:2:5")])
    assert cvr.resolved_cross_verse_items("slug", _entries(("root", "2:1:1-2:2:5")), set()) == []


def test_root_already_live_unresolved_is_skipped(monkeypatch):
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:2:5")])
    assert cvr.resolved_cross_verse_items("slug", _entries(("root", "2:1:1-2:1:1")), {"root"}) == []


def test_deleted_root_is_dropped(monkeypatch):
    _stub_history(monkeypatch, [_split_op("root", "2:1:1-2:2:5")])
    assert cvr.resolved_cross_verse_items("slug", _entries(("other", "2:3:1-2:3:4")), set()) == []


def test_roots_are_deduped_across_batches(monkeypatch):
    _stub_history(
        monkeypatch,
        [_split_op("root", "2:1:1-2:2:5"), _split_op("root", "2:1:1-2:1:1")],
    )
    assert cvr.cross_verse_split_roots("slug") == ["root"]
