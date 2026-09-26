"""Tests for ``services.auto_split`` — pure sidecar lookup contract.

The Inspector no longer calls MFA at request time; ``compute_auto_split``
now just resolves the precomputed entry from ``load_auto_split`` (which
reads ``<reciter>/auto_split_v1.json`` emitted offline by
``qua_shared/auto_split_precompute.py``). These tests stub the loader,
not MFA — there is no MFA Space client to mock anymore.
"""

from __future__ import annotations

import pytest

from services import auto_split


def _patch_sidecar(monkeypatch, by_uid: dict[str, object]) -> None:
    """Replace ``load_auto_split`` with a fixed in-memory map."""
    monkeypatch.setattr(
        auto_split,
        "load_auto_split",
        lambda _r: (by_uid, {"created_at": "2026-01-01T00:00:00Z"}),
    )
    monkeypatch.setattr(auto_split, "load_hidden_pause", lambda _r: ({}, None))
    monkeypatch.setattr(auto_split, "load_missed_waqf", lambda _r: ({}, None))


def _patch_missed_waqf(monkeypatch, by_uid: dict[str, object]) -> None:
    monkeypatch.setattr(
        auto_split, "load_missed_waqf", lambda _r: (by_uid, {"kind": "missed_waqf"})
    )


def _patch_hidden_pause(monkeypatch, by_uid: dict[str, object]) -> None:
    monkeypatch.setattr(
        auto_split, "load_hidden_pause", lambda _r: (by_uid, {"kind": "hidden_pause"})
    )


def _patch_detailed(monkeypatch, entries: list[dict]) -> None:
    monkeypatch.setattr(auto_split, "load_detailed", lambda _r: entries)


# ---------------------------------------------------------------------------
# Sidecar hits
# ---------------------------------------------------------------------------


def test_sidecar_hit_cross_verse(monkeypatch):
    """Cross-verse uid in sidecar → cursors + refs + kind + source=sidecar."""
    _patch_sidecar(
        monkeypatch,
        {
            "u1": {
                "cursors": [13_730],
                "refs": ["1:3:1-1:3:2", "1:4:1-1:4:3"],
                "kind": "cross_verse",
            },
        },
    )
    out = auto_split.compute_auto_split("any-reciter", 1, "u1")
    assert out == {
        "cursors": [13_730],
        "refs": ["1:3:1-1:3:2", "1:4:1-1:4:3"],
        "kind": "cross_verse",
        "source": "sidecar",
    }


def test_sidecar_hit_n_cursor_cross_verse(monkeypatch):
    """3-verse cross-verse seg → 2 cursors + 3 refs."""
    _patch_sidecar(
        monkeypatch,
        {
            "u_3v": {
                "cursors": [11_500, 12_500],
                "refs": ["37:1:1-37:1:7", "37:2:1-37:2:3", "37:3:1-37:3:2"],
                "kind": "cross_verse",
            },
        },
    )
    out = auto_split.compute_auto_split("any-reciter", 37, "u_3v")
    assert out["cursors"] == [11_500, 12_500]
    assert out["refs"] == [
        "37:1:1-37:1:7",
        "37:2:1-37:2:3",
        "37:3:1-37:3:2",
    ]
    assert out["kind"] == "cross_verse"
    assert out["source"] == "sidecar"


def test_sidecar_hit_repetition(monkeypatch):
    _patch_sidecar(
        monkeypatch,
        {
            "u_rep": {
                "cursors": [11_500],
                "refs": ["1:1:1-1:1:3", "1:1:2-1:1:4"],
                "kind": "repetition",
            },
        },
    )
    out = auto_split.compute_auto_split("any-reciter", 1, "u_rep")
    assert out["kind"] == "repetition"
    assert out["source"] == "sidecar"
    assert out["cursors"] == [11_500]


# ---------------------------------------------------------------------------
# Misses
# ---------------------------------------------------------------------------


def test_miss_returns_null_cursors_and_kind_for_cross_verse(monkeypatch):
    """Seg is cross-verse candidate but no sidecar entry → kind set, cursors None."""
    _patch_sidecar(monkeypatch, {})
    _patch_detailed(
        monkeypatch,
        [
            {
                "ref": "37",
                "segments": [
                    {
                        "segment_uid": "u_miss",
                        "matched_ref": "37:1:1-37:3:2",
                        "time_start": 10_000,
                        "time_end": 16_000,
                    }
                ],
            }
        ],
    )
    out = auto_split.compute_auto_split("r", 37, "u_miss")
    assert out == {
        "cursors": None,
        "refs": None,
        "kind": "cross_verse",
        "source": "miss",
    }


def test_miss_returns_null_cursors_and_kind_for_repetition(monkeypatch):
    _patch_sidecar(monkeypatch, {})
    _patch_detailed(
        monkeypatch,
        [
            {
                "ref": "1",
                "segments": [
                    {
                        "segment_uid": "u_rep_miss",
                        "matched_ref": "1:1:1-1:1:4",
                        "time_start": 0,
                        "time_end": 1000,
                        "wrap_word_ranges": [["1:1:2", "1:1:3", "1:1:4"]],
                    }
                ],
            }
        ],
    )
    out = auto_split.compute_auto_split("r", 1, "u_rep_miss")
    assert out == {
        "cursors": None,
        "refs": None,
        "kind": "repetition",
        "source": "miss",
    }


def test_miss_on_non_candidate_seg_returns_kind_none(monkeypatch):
    """Single-verse seg with no wrap → not an auto-split candidate → kind=None."""
    _patch_sidecar(monkeypatch, {})
    _patch_detailed(
        monkeypatch,
        [
            {
                "ref": "1",
                "segments": [
                    {
                        "segment_uid": "u_plain",
                        "matched_ref": "1:1:1-1:1:4",
                        "time_start": 0,
                        "time_end": 1000,
                    }
                ],
            }
        ],
    )
    out = auto_split.compute_auto_split("r", 1, "u_plain")
    assert out == {
        "cursors": None,
        "refs": None,
        "kind": None,
        "source": "miss",
    }


def test_miss_unknown_uid_falls_back_to_detailed_lookup(monkeypatch):
    """Sidecar empty + uid not in detailed.json → kind None (post-edit ghost)."""
    _patch_sidecar(monkeypatch, {})
    _patch_detailed(monkeypatch, [])
    out = auto_split.compute_auto_split("r", 37, "u_ghost")
    assert out["kind"] is None
    assert out["source"] == "miss"


# ---------------------------------------------------------------------------
# Defensive shapes
# ---------------------------------------------------------------------------


def test_sidecar_entry_missing_refs_treated_as_miss(monkeypatch):
    """Half-populated entry shouldn't ship to FE — degrade to miss."""
    _patch_sidecar(monkeypatch, {"u": {"cursors": [1000], "refs": None, "kind": "cross_verse"}})
    _patch_detailed(
        monkeypatch,
        [
            {
                "ref": "37",
                "segments": [
                    {
                        "segment_uid": "u",
                        "matched_ref": "37:1:1-37:3:2",
                        "time_start": 0,
                        "time_end": 5000,
                    }
                ],
            }
        ],
    )
    out = auto_split.compute_auto_split("r", 37, "u")
    assert out["source"] == "miss"
    assert out["cursors"] is None


@pytest.mark.parametrize(
    "ref",
    [
        "37:1:1-37:1:4",  # single verse → not cross-verse
        "garbage",  # malformed
    ],
)
def test_non_candidate_refs_classified_as_kind_none(monkeypatch, ref):
    _patch_sidecar(monkeypatch, {})
    _patch_detailed(
        monkeypatch,
        [
            {
                "ref": "37",
                "segments": [
                    {
                        "segment_uid": "u",
                        "matched_ref": ref,
                        "time_start": 0,
                        "time_end": 1000,
                    }
                ],
            }
        ],
    )
    out = auto_split.compute_auto_split("r", 37, "u")
    assert out["kind"] is None
    assert out["source"] == "miss"


# ---------------------------------------------------------------------------
# Bulk map (load_auto_split_map) — the FE-preload payload
# ---------------------------------------------------------------------------


def test_load_auto_split_map_returns_all_valid_entries(monkeypatch):
    """Every fully-shaped sidecar hit is returned, keyed by uid."""
    _patch_sidecar(
        monkeypatch,
        {
            "u1": {
                "cursors": [13_730],
                "refs": ["1:3:1-1:3:2", "1:4:1-1:4:3"],
                "kind": "cross_verse",
            },
            "u2": {
                "cursors": [11_500],
                "refs": ["1:1:1-1:1:3", "1:1:2-1:1:4"],
                "kind": "repetition",
            },
        },
    )
    out = auto_split.load_auto_split_map("any-reciter")
    assert out == {
        "u1": {"cursors": [13_730], "refs": ["1:3:1-1:3:2", "1:4:1-1:4:3"], "kind": "cross_verse"},
        "u2": {"cursors": [11_500], "refs": ["1:1:1-1:1:3", "1:1:2-1:1:4"], "kind": "repetition"},
    }


def test_load_auto_split_map_drops_half_shaped_entries(monkeypatch):
    """Entries missing cursors / refs / kind are dropped, not shipped."""
    _patch_sidecar(
        monkeypatch,
        {
            "ok": {"cursors": [1000], "refs": ["1:1:1", "1:1:2"], "kind": "cross_verse"},
            "no_refs": {"cursors": [1000], "refs": None, "kind": "cross_verse"},
            "no_curs": {"cursors": None, "refs": ["a", "b"], "kind": "cross_verse"},
            "no_kind": {"cursors": [1000], "refs": ["a", "b"], "kind": None},
            "garbage": "not-a-dict",
        },
    )
    out = auto_split.load_auto_split_map("r")
    assert set(out) == {"ok"}


def test_load_auto_split_map_empty_sidecar(monkeypatch):
    """Absent / empty sidecar → empty map (FE treats every row as a miss)."""
    _patch_sidecar(monkeypatch, {})
    assert auto_split.load_auto_split_map("r") == {}


# ---------------------------------------------------------------------------
# hidden_pause_v1 merge
# ---------------------------------------------------------------------------


def test_hidden_pause_entries_with_refs_merge_as_hidden_pause_kind(monkeypatch):
    _patch_sidecar(
        monkeypatch, {"u_cv": {"cursors": [1], "refs": ["a", "b"], "kind": "cross_verse"}}
    )
    _patch_hidden_pause(
        monkeypatch,
        {
            "u_hp": {"cursors": [5000], "refs": ["2:5:1-2:5:3", "2:5:4-2:5:6"], "score": 2450},
            "u_norefs": {"cursors": [7000], "refs": None, "score": 1040},
            "u_cv": {"cursors": [9], "refs": ["x", "y"], "score": 1},
        },
    )
    out = auto_split.load_auto_split_map("r")
    assert out["u_hp"] == {
        "cursors": [5000],
        "refs": ["2:5:1-2:5:3", "2:5:4-2:5:6"],
        "kind": "hidden_pause",
    }
    assert "u_norefs" not in out
    assert out["u_cv"]["kind"] == "cross_verse"


def test_compute_auto_split_resolves_hidden_pause_hit(monkeypatch):
    _patch_sidecar(monkeypatch, {})
    _patch_hidden_pause(monkeypatch, {"u_hp": {"cursors": [5000], "refs": ["a", "b"]}})
    out = auto_split.compute_auto_split("r", 2, "u_hp")
    assert out == {
        "cursors": [5000],
        "refs": ["a", "b"],
        "kind": "hidden_pause",
        "source": "sidecar",
    }


# ---------------------------------------------------------------------------
# missed_waqf_v1 merge
# ---------------------------------------------------------------------------


def test_missed_waqf_entries_with_refs_merge_as_missed_waqf_kind(monkeypatch):
    _patch_sidecar(
        monkeypatch, {"u_cv": {"cursors": [1], "refs": ["a", "b"], "kind": "cross_verse"}}
    )
    _patch_missed_waqf(
        monkeypatch,
        {
            "u_mw": {"cursors": [4000], "refs": ["2:5:1-2:5:3", "2:5:4-2:5:6"], "score": 1300},
            "u_norefs": {"cursors": [7000], "refs": None, "score": 1040},
            "u_cv": {"cursors": [9], "refs": ["x", "y"], "score": 1},
        },
    )
    out = auto_split.load_auto_split_map("r")
    assert out["u_mw"] == {
        "cursors": [4000],
        "refs": ["2:5:1-2:5:3", "2:5:4-2:5:6"],
        "kind": "missed_waqf",
    }
    assert "u_norefs" not in out
    assert out["u_cv"]["kind"] == "cross_verse"


def test_missed_waqf_entry_wins_over_hidden_pause_for_same_uid(monkeypatch):
    _patch_sidecar(monkeypatch, {})
    _patch_missed_waqf(monkeypatch, {"u": {"cursors": [4000], "refs": ["a", "b"]}})
    _patch_hidden_pause(monkeypatch, {"u": {"cursors": [5000], "refs": ["c", "d"]}})
    assert auto_split.compute_auto_split("r", 2, "u") == {
        "cursors": [4000],
        "refs": ["a", "b"],
        "kind": "missed_waqf",
        "source": "sidecar",
    }
