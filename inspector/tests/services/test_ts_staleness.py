"""Unit tests for the computed TS-track staleness signal.

``ts_stale_info`` compares the edit-history batches against the last TS
generation time. Timestamp-affecting edits saved after the generation make the
generated timestamps stale; annotation-only edits and pre-generation edits do
not. The edit-history read is stubbed via ``parse_history_for_reciter`` so the
test exercises the comparison + op-type filtering directly.

The mtime-gate tests cover the fast path: on a mounted (or filesystem-backed)
bucket the edit-history file's mtime short-circuits the JSONL read entirely when
nothing was saved after generation — the bulk of the releases-status latency
the per-reciter loop used to pay.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from services.activity import history_query
from services.segments import ts_staleness

_GEN_AT = "2026-03-01T12:00:00Z"


def _batch(saved_at: str, op_type: str, chapter: int | None = None) -> dict:
    b: dict = {"saved_at_utc": saved_at, "operations": [{"op_type": op_type}]}
    if chapter is not None:
        b["chapter"] = chapter
    return b


def _patch_history(monkeypatch, batches):
    monkeypatch.setattr(history_query, "parse_history_for_reciter", lambda _slug: batches)


def test_structural_edit_after_generation_is_stale(monkeypatch):
    _patch_history(
        monkeypatch,
        [
            _batch("2026-03-02T09:00:00Z", "trim_segment", chapter=5),
            _batch("2026-03-03T09:00:00Z", "split_segment", chapter=12),
        ],
    )
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info == {
        "stale_since": "2026-03-02T09:00:00Z",
        "last_edit_at": "2026-03-03T09:00:00Z",
        "edits_since": 2,
        "affected_chapters": [5, 12],
    }


def test_reference_edit_after_generation_is_stale(monkeypatch):
    """edit_reference changes which ayah a segment maps to → artifact changes."""
    _patch_history(monkeypatch, [_batch("2026-03-02T09:00:00Z", "edit_reference")])
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info is not None and info["edits_since"] == 1


def test_annotation_only_edits_after_generation_are_not_stale(monkeypatch):
    _patch_history(
        monkeypatch,
        [
            _batch("2026-03-02T09:00:00Z", "confirm_reference"),
            _batch("2026-03-02T10:00:00Z", "ignore_issue"),
            _batch("2026-03-02T11:00:00Z", "flag_segment"),
        ],
    )
    assert ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT) is None


def test_wasl_relabel_after_generation_is_stale(monkeypatch):
    """``is_wasl`` drives how the shard builder chains readings, so flipping
    it after a generation makes the timestamps stale."""
    _patch_history(monkeypatch, [_batch("2026-03-02T12:00:00Z", "set_is_wasl")])
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info is not None and info["edits_since"] == 1


def test_edit_before_generation_is_not_stale(monkeypatch):
    _patch_history(monkeypatch, [_batch("2026-02-01T09:00:00Z", "trim_segment")])
    assert ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT) is None


def test_edit_history_read_failure_is_not_stale(monkeypatch):
    """A failed edit-history read (e.g. bucket unavailable) must not propagate —
    the status grid calls this per reciter and one failure can't 500 the page."""

    def _boom(_slug):
        raise RuntimeError("bucket 401")

    monkeypatch.setattr(history_query, "parse_history_for_reciter", _boom)
    assert ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT) is None


def test_mixed_batch_counts_only_affecting_and_keeps_earliest(monkeypatch):
    _patch_history(
        monkeypatch,
        [
            _batch("2026-03-05T09:00:00Z", "merge_segments", chapter=2),
            _batch("2026-03-02T09:00:00Z", "delete_segment", chapter=2),  # earliest affecting
            _batch("2026-03-04T09:00:00Z", "ignore_issue", chapter=9),  # excluded
        ],
    )
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info == {
        "stale_since": "2026-03-02T09:00:00Z",
        "last_edit_at": "2026-03-05T09:00:00Z",
        "edits_since": 2,
        "affected_chapters": [2],  # chapter 9 (ignore_issue) excluded
    }


def test_affected_chapters_union_single_and_multi_chapter_batches(monkeypatch):
    """``chapter`` (Inspector save) and ``chapters`` (pipeline) both contribute."""
    multi = {
        "saved_at_utc": "2026-03-02T09:00:00Z",
        "operations": [{"op_type": "split_segment"}],
        "chapters": [30, 31],
    }
    _patch_history(
        monkeypatch,
        [multi, _batch("2026-03-03T09:00:00Z", "trim_segment", chapter=1)],
    )
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info is not None
    assert info["affected_chapters"] == [1, 30, 31]


# --- mtime fast-path gate ---------------------------------------------------


class _StubBackend:
    """Minimal storage backend exposing only ``local_path`` for the mtime gate."""

    def __init__(self, path):
        self._path = path

    def local_path(self, _bucket_path):
        return self._path


def _history_file(tmp_path, *, mtime: datetime):
    p = tmp_path / "edit_history.jsonl"
    p.write_text('{"saved_at_utc": "x"}\n', encoding="utf-8")
    epoch = mtime.timestamp()
    os.utime(p, (epoch, epoch))
    return p


def test_unedited_since_generation_skips_history_read(monkeypatch, tmp_path):
    """mtime <= produced_at on the mounted file → not stale, JSONL never parsed."""
    f = _history_file(tmp_path, mtime=datetime(2026, 2, 15, tzinfo=UTC))
    monkeypatch.setattr(ts_staleness, "get_backend", lambda: _StubBackend(f))
    calls = {"n": 0}

    def _spy(_slug):
        calls["n"] += 1
        return []

    monkeypatch.setattr(history_query, "parse_history_for_reciter", _spy)
    assert ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT) is None
    assert calls["n"] == 0


def test_edited_after_generation_falls_through_to_history_read(monkeypatch, tmp_path):
    """mtime > produced_at → gate doesn't fire; the parse path computes staleness."""
    f = _history_file(tmp_path, mtime=datetime(2026, 3, 15, tzinfo=UTC))
    monkeypatch.setattr(ts_staleness, "get_backend", lambda: _StubBackend(f))
    _patch_history(monkeypatch, [_batch("2026-03-10T09:00:00Z", "trim_segment", chapter=7)])
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info is not None
    assert info["edits_since"] == 1
    assert info["affected_chapters"] == [7]


def test_no_mounted_path_falls_through_to_history_read(monkeypatch):
    """Unmounted (``local_path`` None) → gate can't fire, parse path runs (dev unchanged)."""
    monkeypatch.setattr(ts_staleness, "get_backend", lambda: _StubBackend(None))
    _patch_history(monkeypatch, [_batch("2026-03-10T09:00:00Z", "merge_segments", chapter=3)])
    info = ts_staleness.ts_stale_info("slug", produced_at=_GEN_AT)
    assert info is not None
    assert info["edits_since"] == 1
