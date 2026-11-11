"""Cache invariants for the post-refactor segments-tab cold-load path.

Covers:

- ``append_history_batch`` keeps the parsed JSONL list in step with disk so
  the next reader doesn't re-parse.
- ``batch_changes_segment_set`` correctly gates auto-split eviction
  (split / merge / auto-fix / delete → True; trim / ref-edit → False).
- The split-group index built from the cached batch list matches a fresh
  parse byte-for-byte (post-save + post-undo).
- LRU eviction on ``_KeyedCache.set`` evicts the oldest write and re-set
  promotes correctly.
"""

from __future__ import annotations

import pytest

from services.activity.history_query import build_split_group_index
from services.storage import cache
from services.storage.cache import (
    _KeyedCache,
    append_history_batch,
    batch_changes_segment_set,
)

# ---------------------------------------------------------------------------
# batch_changes_segment_set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op_type,expected",
    [
        ("split_segment", True),
        ("merge_segments", True),
        ("auto_fix_missing_word", True),
        ("delete_segment", True),
        ("trim_segment", False),
        ("edit_reference", False),
        ("ignore_issue", False),
        ("boundary_adjustment", False),
    ],
)
def test_batch_changes_segment_set_by_op_type(op_type, expected):
    assert batch_changes_segment_set({"operations": [{"op_type": op_type}]}) is expected


def test_batch_changes_segment_set_also_checks_kind_field():
    """Some FE ops carry ``kind`` instead of ``op_type``."""
    assert batch_changes_segment_set({"operations": [{"kind": "split_segment"}]}) is True
    assert batch_changes_segment_set({"operations": [{"kind": "trim_segment"}]}) is False


def test_batch_changes_segment_set_empty_batch():
    assert batch_changes_segment_set({"operations": []}) is False
    assert batch_changes_segment_set({}) is False


def test_batch_changes_segment_set_mixed_ops():
    """Any one mutating op flips True even when other ops are non-mutating."""
    batch = {
        "operations": [
            {"op_type": "trim_segment"},
            {"op_type": "split_segment"},
            {"op_type": "edit_reference"},
        ]
    }
    assert batch_changes_segment_set(batch) is True


# ---------------------------------------------------------------------------
# append_history_batch
# ---------------------------------------------------------------------------


def test_append_history_batch_no_op_when_cache_empty():
    """If the cache hasn't been hydrated for a reciter, append is a no-op
    so the next cold read pulls the canonical disk state."""
    slug = "test-append-empty"
    cache.pop_seg_history_batches(slug)
    append_history_batch(slug, {"batch_id": "b1", "operations": []})
    assert cache.get_seg_history_batches(slug) is None


def test_append_history_batch_extends_hydrated_list():
    slug = "test-append-hydrated"
    cache.set_seg_history_batches(slug, [{"batch_id": "b0", "operations": []}])

    append_history_batch(slug, {"batch_id": "b1", "operations": []})
    append_history_batch(slug, {"batch_id": "b2", "operations": []})

    batches = cache.get_seg_history_batches(slug)
    assert batches is not None
    assert [b["batch_id"] for b in batches] == ["b0", "b1", "b2"]
    cache.pop_seg_history_batches(slug)


def test_append_history_batch_does_not_mutate_caller_list():
    """The append helper builds a new list — the caller's reference to
    the original cache list stays unmodified."""
    slug = "test-append-immutable"
    original = [{"batch_id": "b0", "operations": []}]
    cache.set_seg_history_batches(slug, original)

    append_history_batch(slug, {"batch_id": "b1", "operations": []})

    assert len(original) == 1, "caller's list snapshot should not be mutated"
    cache.pop_seg_history_batches(slug)


# ---------------------------------------------------------------------------
# split-group index byte-equality across cache shapes
# ---------------------------------------------------------------------------


def _split_batch(parent_uid: str, *children: str, batch_id: str) -> dict:
    return {
        "batch_id": batch_id,
        "operations": [
            {
                "op_id": f"op-{batch_id}",
                "op_type": "split_segment",
                "targets_before": [{"segment_uid": parent_uid}],
                "targets_after": [{"segment_uid": c} for c in children],
            }
        ],
    }


def test_split_group_index_byte_equal_after_save_append():
    """Building the index from (cached batches + appended new batch) must
    equal building it from a fresh parse of the same total batch sequence."""
    slug = "test-split-group-append"
    # Reset caches.
    cache.pop_seg_history_batches(slug)
    cache.pop_seg_split_group_index(slug)

    # Warm cache with two committed splits.
    initial = [
        _split_batch("A", "A1", "A2", batch_id="b1"),
        _split_batch("A1", "A1a", "A1b", batch_id="b2"),
    ]
    cache.set_seg_history_batches(slug, initial)
    index_warm = build_split_group_index(slug)

    # Simulate save: append a third split, pop the derived index, rebuild
    # from the (now-extended) cached batches.
    append_history_batch(slug, _split_batch("A2", "A2a", "A2b", batch_id="b3"))
    cache.pop_seg_split_group_index(slug)
    index_after_append = build_split_group_index(slug)

    # Clear and rebuild from scratch with the full batch list.
    cache.set_seg_history_batches(
        slug,
        [
            _split_batch("A", "A1", "A2", batch_id="b1"),
            _split_batch("A1", "A1a", "A1b", batch_id="b2"),
            _split_batch("A2", "A2a", "A2b", batch_id="b3"),
        ],
    )
    cache.pop_seg_split_group_index(slug)
    index_fresh = build_split_group_index(slug)

    assert index_after_append == index_fresh, (
        "incremental cache (append + pop derived) must produce the same "
        "split-group index as a cold parse"
    )
    # Sanity: warm cache had fewer descendants for root A than after the
    # third split was appended.
    assert len(index_warm.get("A", [])) < len(index_after_append.get("A", []))

    # A's transitive closure post-append covers A1, A2, A1a, A1b, A2a, A2b.
    closure_a = set(index_after_append.get("A", []))
    assert closure_a == {"A1", "A2", "A1a", "A1b", "A2a", "A2b"}

    cache.pop_seg_history_batches(slug)
    cache.pop_seg_split_group_index(slug)


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


def test_keyed_cache_lru_evicts_oldest_on_overflow():
    kc: _KeyedCache[int] = _KeyedCache(max_size=3)
    kc.set("a", 1)
    kc.set("b", 2)
    kc.set("c", 3)
    assert kc.get("a") == 1
    kc.set("d", 4)
    # a was the oldest write → evicted.
    assert kc.get("a") is None
    assert kc.get("b") == 2
    assert kc.get("c") == 3
    assert kc.get("d") == 4


def test_keyed_cache_re_set_promotes_existing_key():
    """Re-setting an existing key should mark it most-recently-set so it
    survives the next eviction."""
    kc: _KeyedCache[int] = _KeyedCache(max_size=3)
    kc.set("a", 1)
    kc.set("b", 2)
    kc.set("c", 3)
    kc.set("a", 10)  # re-set a — promotes it.
    kc.set("d", 4)  # b is now the oldest → evicted.
    assert kc.get("a") == 10
    assert kc.get("b") is None
    assert kc.get("c") == 3
    assert kc.get("d") == 4


def test_keyed_cache_get_does_not_promote():
    """Eviction tracks write-time, not read-time."""
    kc: _KeyedCache[int] = _KeyedCache(max_size=3)
    kc.set("a", 1)
    kc.set("b", 2)
    kc.set("c", 3)
    # Hammering reads on 'a' should NOT save it from the next set-eviction.
    for _ in range(10):
        assert kc.get("a") == 1
    kc.set("d", 4)
    assert kc.get("a") is None


def test_audio_manifest_cache_holds_every_released_sidecar():
    """The TS manifest walks every released sidecar per rebuild; an LRU smaller than
    that set misses on every entry of a fixed-order walk."""
    from services.storage import cache

    slugs = [f"r{i}" for i in range(200)]
    try:
        for slug in slugs:
            cache.set_audio_manifest_cache(slug, {"chapters": {}})
        assert all(cache.get_audio_manifest_cache(s) is not None for s in slugs)
    finally:
        cache.invalidate_audio_manifest_cache()
