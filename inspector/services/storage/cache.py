"""Centralized cache registry for the inspector server.

Every mutable cache variable lives here with getter/setter/invalidation
functions.  No other module uses ``global`` for cache variables.
"""

import threading
import time as _time
from collections import OrderedDict
from typing import Generic, TypeVar

_T = TypeVar("_T")

# Per-cache LRU ceiling. The Inspector loads one reciter at a time in
# practice, but admin sweeps + concurrent reviewers can stack up; each
# parsed cache entry is small individually (~MB) but grows unboundedly
# across many reciters touched in one process lifetime. 20 covers the
# realistic concurrency ceiling on a single-worker Space with plenty of
# headroom while bounding worst-case RSS.
_KEYED_CACHE_LRU_MAX = 20


class _SingletonCache(Generic[_T]):
    """Holds a single nullable value — replaces a bare ``global`` variable."""

    def __init__(self) -> None:
        self._value: _T | None = None

    def get(self) -> _T | None:
        return self._value

    def set(self, value: _T) -> None:
        self._value = value

    def clear(self) -> None:
        self._value = None


class _KeyedCache(Generic[_T]):
    """LRU-bounded dict keyed by string — replaces a bare ``global`` dict.

    Every ``set`` records the key as most-recently-used; once size exceeds
    ``_KEYED_CACHE_LRU_MAX``, the oldest entry is evicted. ``get`` does
    NOT promote a key — the loaders consult the cache once per request,
    and we want eviction to track write-time (the actual cost we're
    bounding) rather than read frequency.
    """

    def __init__(self, max_size: int = _KEYED_CACHE_LRU_MAX) -> None:
        self._data: OrderedDict[str, _T] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> _T | None:
        return self._data.get(key)

    def set(self, key: str, value: _T) -> None:
        if key in self._data:
            # Mark as most-recently-set so the new value isn't evicted next.
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def pop(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def all(self) -> dict[str, _T]:
        return dict(self._data)


# Timestamps tab — local-mode shard cache lives in `services/timestamps.py`
# (manifest + per-chapter gzipped bytes, lazy-built). Nothing for this tab
# is registered here: the legacy `_ts` / `_ts_reciters` slots that pinned
# full ``timestamps_full.json`` docs in memory are gone.


# Segments
_seg: _KeyedCache[list[dict]] = _KeyedCache()
_seg_meta: _KeyedCache[dict] = _KeyedCache()
_seg_verses: _KeyedCache[tuple] = _KeyedCache()
_seg_resolved_by_edit: _KeyedCache[dict[str, set[str]]] = _KeyedCache()
# Low-Confidence v2 sidecar — uid set + meta dict (or None when sidecar absent).
_seg_probe_v2: _KeyedCache[tuple[set[str], dict | None]] = _KeyedCache()
# Auto-Split sidecar — precomputed cursors/refs by uid, plus meta. Empty dict
# tuple member when sidecar absent so callers don't re-stat on every lookup.
_seg_auto_split: _KeyedCache[tuple[dict[str, dict], dict | None]] = _KeyedCache()
# Boundary-review sidecars (hidden_pause_v1 / false_split_v1 / unmarked_wasl_v1)
# — by-uid map plus meta; empty dict when the sidecar is absent.
_seg_hidden_pause: _KeyedCache[tuple[dict[str, dict], dict | None]] = _KeyedCache()
_seg_false_split: _KeyedCache[tuple[dict[str, dict], dict | None]] = _KeyedCache()
_seg_unmarked_wasl: _KeyedCache[tuple[dict[str, dict], dict | None]] = _KeyedCache()
# pipeline_meta.json sidecar — extraction-time facts (deleted_basmala_chapters,
# generated_at, ...). Immutable post-extraction; NEVER invalidated by save.
_seg_pipeline_meta: _KeyedCache[dict] = _KeyedCache()
# Parsed edit_history.jsonl batches (list[dict]). Filled lazily on first read;
# every save APPENDS the new batch (no full re-parse). Undo also appends a
# revert batch. The cache slot itself is never popped on save/undo — only on
# process restart or explicit invalidation.
_seg_history_batches: _KeyedCache[list[dict]] = _KeyedCache()
# Derived index: uid → list of transitive split-descendant uids. Pure function
# of _seg_history_batches. Filled lazily; **extended on save** when the saved
# batch contains split_segment ops, **popped on undo** (revert negates prior
# ops; incremental append can't model removal — rebuild from cached list).
_seg_split_group_index: _KeyedCache[dict[str, list[str]]] = _KeyedCache()
_seg_edit_history: _KeyedCache[dict] = _KeyedCache()
_seg_history_peaks: _KeyedCache[list[dict]] = _KeyedCache()
# Serialized GET /api/seg/history-peaks response bytes, keyed by reciter. The
# History panel is browsed heavily (incl. anonymous), so cache the orjson bytes
# to skip re-serializing the (canonical b64) record list on every tab open.
# Popped wherever the JSONL is appended (save invalidation + anon write-back).
_seg_history_peaks_response: _KeyedCache[bytes] = _KeyedCache()
# Validation + stats results — recomputed only on cache miss; cleared by
# ``invalidate_seg_caches`` on every save / undo so writers can't read stale data.
_seg_validate_result: _KeyedCache[dict] = _KeyedCache()
_seg_stats_result: _KeyedCache[dict] = _KeyedCache()


#: How long a "there is no detailed.json" answer is trusted. The absence is
#: cached to stop the Reviews drawer doing a bucket round-trip per slug per
#: request, which is a burst-scoped problem — but a delivery can gain the file
#: out of band (a promote run), and the invalidation hooks only fire for a
#: delivery still awaiting alignment. Without a lapse such a slug 404s until the
#: process restarts, and the escape hatch (a save) is unreachable because the
#: editor cannot load. Seconds, so the burst is still absorbed.
_SEG_ABSENT_TTL_S = 30.0

#: ``{reciter: monotonic stamp}`` for the entries above that are empty because
#: the file is MISSING, as opposed to empty because the delivery has no segments.
_seg_absent_at: dict[str, float] = {}


def get_seg_cache(reciter: str) -> list[dict] | None:
    entries = _seg.get(reciter)
    stamped_at = _seg_absent_at.get(reciter)
    if stamped_at is not None and _time.monotonic() - stamped_at > _SEG_ABSENT_TTL_S:
        _seg_absent_at.pop(reciter, None)
        _seg.pop(reciter)
        return None
    return entries


def set_seg_cache(reciter: str, entries: list[dict], *, absent: bool = False) -> None:
    """Cache *reciter*'s entries. ``absent=True`` marks them as a missing file."""
    _seg.set(reciter, entries)
    if absent:
        _seg_absent_at[reciter] = _time.monotonic()
    else:
        _seg_absent_at.pop(reciter, None)


def get_seg_meta(reciter: str) -> dict:
    return _seg_meta.get(reciter) or {}


def set_seg_meta(reciter: str, meta: dict) -> None:
    _seg_meta.set(reciter, meta)


def get_seg_verses_cache(reciter: str):
    return _seg_verses.get(reciter)


def set_seg_verses_cache(reciter: str, data: tuple) -> None:
    _seg_verses.set(reciter, data)


def get_seg_resolved_by_edit(reciter: str) -> dict[str, set[str]] | None:
    return _seg_resolved_by_edit.get(reciter)


def set_seg_resolved_by_edit(reciter: str, index: dict[str, set[str]]) -> None:
    _seg_resolved_by_edit.set(reciter, index)


def get_seg_probe_v2(reciter: str) -> tuple[set[str], dict | None] | None:
    return _seg_probe_v2.get(reciter)


def set_seg_probe_v2(reciter: str, value: tuple[set[str], dict | None]) -> None:
    _seg_probe_v2.set(reciter, value)


def get_seg_auto_split(reciter: str) -> tuple[dict[str, dict], dict | None] | None:
    return _seg_auto_split.get(reciter)


def set_seg_auto_split(reciter: str, value: tuple[dict[str, dict], dict | None]) -> None:
    _seg_auto_split.set(reciter, value)


def get_seg_hidden_pause(reciter: str) -> tuple[dict[str, dict], dict | None] | None:
    return _seg_hidden_pause.get(reciter)


def set_seg_hidden_pause(reciter: str, value: tuple[dict[str, dict], dict | None]) -> None:
    _seg_hidden_pause.set(reciter, value)


def get_seg_false_split(reciter: str) -> tuple[dict[str, dict], dict | None] | None:
    return _seg_false_split.get(reciter)


def set_seg_false_split(reciter: str, value: tuple[dict[str, dict], dict | None]) -> None:
    _seg_false_split.set(reciter, value)


def get_seg_unmarked_wasl(reciter: str) -> tuple[dict[str, dict], dict | None] | None:
    return _seg_unmarked_wasl.get(reciter)


def set_seg_unmarked_wasl(reciter: str, value: tuple[dict[str, dict], dict | None]) -> None:
    _seg_unmarked_wasl.set(reciter, value)


def pop_seg_auto_split(reciter: str) -> None:
    """Pop the auto-split cache for *reciter*.

    Save's surgical-invalidate path (``pop_seg_caches_affected_by_segment_edit``)
    deliberately excludes this cache: split / merge / auto-fix / delete change
    the uid set so the cached cursors per uid become stale, but ref-edits and
    pure trims leave it valid. Callers gate the pop on
    ``batch_changes_segment_set(batch)``.
    """
    _seg_auto_split.pop(reciter)


def get_seg_pipeline_meta(reciter: str) -> dict | None:
    return _seg_pipeline_meta.get(reciter)


def set_seg_pipeline_meta(reciter: str, value: dict) -> None:
    _seg_pipeline_meta.set(reciter, value)


def pop_seg_pipeline_meta(reciter: str) -> None:
    """Explicit eviction (e.g. for the backfill script when it rewrites)."""
    _seg_pipeline_meta.pop(reciter)


# ---------------------------------------------------------------------------
# Edit-history-derived caches (incremental on save, pop+rebuild on undo)
# ---------------------------------------------------------------------------


def get_seg_history_batches(reciter: str) -> list[dict] | None:
    return _seg_history_batches.get(reciter)


def set_seg_history_batches(reciter: str, batches: list[dict]) -> None:
    _seg_history_batches.set(reciter, batches)


def append_history_batch(reciter: str, batch: dict) -> None:
    """Append a freshly-saved batch to the cached parsed list.

    No-op if the cache is empty for this reciter (next read will populate
    it from disk and naturally include the new batch). Save's append-then-
    notify ordering guarantees the batch is already on disk before this
    fires, so a cold reader sees a consistent picture either way.
    """
    cached = _seg_history_batches.get(reciter)
    if cached is None:
        return
    _seg_history_batches.set(reciter, [*cached, batch])


def pop_seg_history_batches(reciter: str) -> None:
    _seg_history_batches.pop(reciter)


def get_seg_split_group_index(reciter: str) -> dict[str, list[str]] | None:
    return _seg_split_group_index.get(reciter)


def set_seg_split_group_index(
    reciter: str,
    index: dict[str, list[str]],
) -> None:
    _seg_split_group_index.set(reciter, index)


def pop_seg_split_group_index(reciter: str) -> None:
    """Pop on undo — revert ops can't be modelled incrementally."""
    _seg_split_group_index.pop(reciter)


def get_seg_edit_history(reciter: str) -> dict | None:
    return _seg_edit_history.get(reciter)


def set_seg_edit_history(reciter: str, value: dict) -> None:
    _seg_edit_history.set(reciter, value)


def get_seg_history_peaks(reciter: str) -> list[dict] | None:
    return _seg_history_peaks.get(reciter)


def set_seg_history_peaks(reciter: str, value: list[dict]) -> None:
    _seg_history_peaks.set(reciter, value)


def get_seg_history_peaks_response(reciter: str) -> bytes | None:
    return _seg_history_peaks_response.get(reciter)


def set_seg_history_peaks_response(reciter: str, value: bytes) -> None:
    _seg_history_peaks_response.set(reciter, value)


def pop_seg_history_peaks_response(reciter: str) -> None:
    _seg_history_peaks_response.pop(reciter)


def get_seg_validate_cache(reciter: str) -> dict | None:
    return _seg_validate_result.get(reciter)


def set_seg_validate_cache(reciter: str, value: dict) -> None:
    _seg_validate_result.set(reciter, value)


def get_seg_stats_cache(reciter: str) -> dict | None:
    return _seg_stats_result.get(reciter)


def set_seg_stats_cache(reciter: str, value: dict) -> None:
    _seg_stats_result.set(reciter, value)


def invalidate_seg_caches(reciter: str) -> None:
    """Remove all segment-related caches for *reciter* and reset reciters list.

    Deliberately does NOT touch:

    - The **peaks LRU response cache** — peaks files are tied to the audio
      bytes and never change as a side effect of edits, so the cached peaks
      response stays valid across any number of segment saves. Evicting it
      here would cost a ~500 ms cold miss on every autosave (every few
      seconds) for nothing. The LRU still naturally evicts under pressure
      (50-entry global ceiling) and on process restart.
    - The **pipeline_meta sidecar cache** — extraction-time facts that no
      user edit can change. Invalidating per save would burn the cache on
      every autosave for zero benefit.

    Add an explicit ``pop_reciter_peaks_response_cache`` call wherever a
    future code path actually rewrites peaks on the bucket; add an explicit
    ``pop_seg_pipeline_meta`` call wherever extraction or backfill rewrites
    the sidecar.
    """
    _seg.pop(reciter)
    _seg_meta.pop(reciter)
    _seg_verses.pop(reciter)
    _seg_resolved_by_edit.pop(reciter)
    _seg_probe_v2.pop(reciter)
    _seg_hidden_pause.pop(reciter)
    _seg_false_split.pop(reciter)
    _seg_unmarked_wasl.pop(reciter)
    _seg_auto_split.pop(reciter)
    _seg_edit_history.pop(reciter)
    _seg_history_peaks.pop(reciter)
    _seg_history_peaks_response.pop(reciter)
    _seg_validate_result.pop(reciter)
    _seg_stats_result.pop(reciter)
    # _seg_history_batches and _seg_split_group_index are NOT popped here —
    # callers that mean it (save / undo) use the surgical helpers below so
    # the parsed list survives autosave warm paths.


def pop_seg_caches_affected_by_segment_edit(reciter: str) -> None:
    """Surgical eviction of caches a segment edit invalidates.

    Excludes:
    - ``_seg_pipeline_meta`` — extraction-time, immutable.
    - ``_seg_history_batches`` — appended in place (no re-parse).
    - ``_seg_split_group_index`` — extended in place on save; explicitly
      popped on undo via ``pop_seg_split_group_index``.
    - ``_seg_auto_split`` — only invalidated when the uid set changes;
      callers gate the pop on ``batch_changes_segment_set``.

    Callers should follow with ``append_history_batch`` (and, on save,
    ``set_seg_split_group_index`` to extend the index incrementally).
    """
    _seg.pop(reciter)
    _seg_meta.pop(reciter)
    _seg_verses.pop(reciter)
    _seg_resolved_by_edit.pop(reciter)
    _seg_probe_v2.pop(reciter)
    _seg_hidden_pause.pop(reciter)
    _seg_false_split.pop(reciter)
    _seg_unmarked_wasl.pop(reciter)
    _seg_edit_history.pop(reciter)
    _seg_history_peaks.pop(reciter)
    _seg_history_peaks_response.pop(reciter)
    _seg_validate_result.pop(reciter)
    _seg_stats_result.pop(reciter)


def batch_changes_segment_set(batch: dict) -> bool:
    """True when at least one op in ``batch`` mutates the seg uid set.

    Used to decide whether ``_seg_auto_split`` needs eviction — split / merge /
    auto-fix create new uids, trim / ref-edit do not.
    """
    _MUTATING_OPS = {
        "split_segment",
        "merge_segments",
        "auto_fix_missing_word",
        "delete_segment",
    }
    for op in batch.get("operations") or []:
        if op.get("op_type") in _MUTATING_OPS or op.get("kind") in _MUTATING_OPS:
            return True
    return False


# Peaks (thread-safe — manually coded)
_PEAKS_CACHE: dict[str, dict[str, dict]] = {}
_PEAKS_LOCK = threading.Lock()


def get_peaks_lock() -> threading.Lock:
    return _PEAKS_LOCK


def get_peaks_cache(reciter: str) -> dict[str, dict]:
    return _PEAKS_CACHE.get(reciter, {})


def set_peaks_for_url(reciter: str, url: str, data: dict) -> None:
    with _PEAKS_LOCK:
        if reciter not in _PEAKS_CACHE:
            _PEAKS_CACHE[reciter] = {}
        _PEAKS_CACHE[reciter][url] = data


# Peaks response cache — bounded-LRU for /api/seg/peaks GET responses.
#
# Keyed by (reciter, sorted_chapter_tuple). Value is the SERIALIZED JSON body
# (bytes) ready to send. Caching bytes (not the parsed dict) skips re-running
# jsonify on every hit -- for the worst chapter (husary ch2, 119k peak tuples)
# jsonify costs ~1.5-2s of single-worker CPU, so caching the dict and
# re-serializing made warm requests indistinguishable from cold ones in
# practice. Compression (flask-compress) runs on top of this, but the cache
# stays at the JSON-bytes layer so Vary/Accept-Encoding negotiation still
# works correctly per request.
#
# Eviction is global (not per-reciter) so we cap total RAM regardless of how
# many reciters a user browses in one session. ~50 entries × avg ~200 KiB
# uncompressed bytes ≈ ~10 MiB ceiling.
#
# Not evicted on segment save/undo: ``invalidate_seg_caches`` deliberately
# leaves it (peaks track immutable audio bytes, not edits). It sheds only via
# its own 50-entry LRU and an explicit ``pop_reciter_peaks_response_cache``
# wherever a path actually rewrites bucket peaks.
_PEAKS_RESPONSE_CACHE: "OrderedDict[tuple[str, tuple], bytes]" = OrderedDict()
_PEAKS_RESPONSE_MAX = 50
_PEAKS_RESPONSE_LOCK = threading.Lock()


def get_peaks_response_cache(reciter: str, chapters: tuple) -> bytes | None:
    """Return the cached serialized response bytes for ``(reciter, chapters)``."""
    key = (reciter, chapters)
    with _PEAKS_RESPONSE_LOCK:
        if key in _PEAKS_RESPONSE_CACHE:
            _PEAKS_RESPONSE_CACHE.move_to_end(key)  # LRU touch
            return _PEAKS_RESPONSE_CACHE[key]
    return None


def set_peaks_response_cache(reciter: str, chapters: tuple, value: bytes) -> None:
    """Store serialized response bytes. Evicts oldest entries when full."""
    key = (reciter, chapters)
    with _PEAKS_RESPONSE_LOCK:
        _PEAKS_RESPONSE_CACHE[key] = value
        _PEAKS_RESPONSE_CACHE.move_to_end(key)
        while len(_PEAKS_RESPONSE_CACHE) > _PEAKS_RESPONSE_MAX:
            _PEAKS_RESPONSE_CACHE.popitem(last=False)


def pop_reciter_peaks_response_cache(reciter: str) -> None:
    """Drop every cached response keyed by ``reciter`` (any chapter set)."""
    with _PEAKS_RESPONSE_LOCK:
        stale = [k for k in _PEAKS_RESPONSE_CACHE if k[0] == reciter]
        for k in stale:
            _PEAKS_RESPONSE_CACHE.pop(k, None)


def clear_peaks_response_cache() -> None:
    """Drop every cached response. Used by tests to prevent cross-test bleed."""
    with _PEAKS_RESPONSE_LOCK:
        _PEAKS_RESPONSE_CACHE.clear()


# Phoneme substitution pairs (lazy singleton)
_phoneme_sub_pairs: _SingletonCache[set[frozenset]] = _SingletonCache()


def get_phoneme_sub_pairs_cache():
    return _phoneme_sub_pairs.get()


def set_phoneme_sub_pairs_cache(pairs: set[frozenset]) -> None:
    _phoneme_sub_pairs.set(pairs)


# Audio URL
_audio_url: _KeyedCache[dict] = _KeyedCache()


def get_audio_url_cache(key: str) -> dict | None:
    return _audio_url.get(key)


def set_audio_url_cache(key: str, urls: dict) -> None:
    _audio_url.set(key, urls)


def pop_audio_url_cache(slug: str) -> None:
    """Evict every serialized audio-URL projection for one exact slug."""
    suffix = f"/{slug}"
    for key in _audio_url.all():
        if key.endswith(suffix):
            _audio_url.pop(key)


# Audio manifest sidecar (catalog/audio_manifest/<slug>.json) + derived
# URL → chapter-key inverse index. Both populated together by
# ``services/audio/audio_meta._load_sidecar`` on first read. The inverse
# index turns ``chapter_for_url`` / ``chapter_meta_for_url`` from O(N)
# linear scans into O(1) dict lookups on the peaks fan-out hot path.
# Lifecycle matches every other per-reciter cache: LRU-bounded, popped by
# ``pop_audio_manifest_cache`` when a future probe-refresh path lands.
_audio_manifest: _KeyedCache[dict] = _KeyedCache()
_audio_manifest_url_index: _KeyedCache[dict[str, str]] = _KeyedCache()


def get_audio_manifest_cache(slug: str) -> dict | None:
    return _audio_manifest.get(slug)


def set_audio_manifest_cache(slug: str, doc: dict) -> None:
    _audio_manifest.set(slug, doc)


def pop_audio_manifest_cache(slug: str) -> None:
    _audio_manifest.pop(slug)
    _audio_manifest_url_index.pop(slug)


def get_audio_manifest_url_index_cache(slug: str) -> dict[str, str] | None:
    return _audio_manifest_url_index.get(slug)


def set_audio_manifest_url_index_cache(slug: str, idx: dict[str, str]) -> None:
    _audio_manifest_url_index.set(slug, idx)


def invalidate_audio_manifest_cache() -> None:
    """Drop ALL cached audio-manifest sidecars + their URL indexes.

    The TS manifest build derives ``vbr_chapters`` from these sidecars and the
    ``/api/audio/surahs`` route serves their per-chapter URLs; clearing them
    alongside a manifest rebuild guards against a stale sidecar (e.g. a
    re-extracted reciter) leaking old URLs."""
    _audio_manifest.clear()
    _audio_manifest_url_index.clear()


# Audio cache status (thread-safe). Note: the audio download-progress dict
# (_AUDIO_DL_PROGRESS + helpers) lived here until the prefetch worker was
# removed; no background audio worker remains.
_AUDIO_CACHE_STATUS: dict[str, dict] = {}


def get_audio_cache_status(reciter: str) -> dict | None:
    return _AUDIO_CACHE_STATUS.get(reciter)


def set_audio_cache_status(reciter: str, status: dict) -> None:
    _AUDIO_CACHE_STATUS[reciter] = status


def pop_audio_cache_status(reciter: str) -> None:
    _AUDIO_CACHE_STATUS.pop(reciter, None)


# Word counts
_word_counts: _SingletonCache[dict[tuple[int, int], int]] = _SingletonCache()


def get_word_counts_cache():
    return _word_counts.get()


def set_word_counts_cache(wc: dict[tuple[int, int], int]) -> None:
    _word_counts.set(wc)


# Single-word verses — derived from word_counts, computed once and cached
# alongside it. Used by the classifier's muqattaat / basmala_amin rules and
# the save-time persisted-classifier-fields stamp. Three callsites used to
# rebuild this O(6236) comprehension fresh each call; the singleton matches
# the lifetime of ``_word_counts`` (immutable post-boot).
_single_word_verses: _SingletonCache[set[tuple[int, int]]] = _SingletonCache()


def get_single_word_verses_cache():
    return _single_word_verses.get()


def set_single_word_verses_cache(swv: set[tuple[int, int]]) -> None:
    _single_word_verses.set(swv)


# QPC / DK data
_qpc: _SingletonCache[dict[str, dict]] = _SingletonCache()
_dk: _SingletonCache[dict[str, dict]] = _SingletonCache()
# Flat ``"surah:ayah:word" -> text`` projection of _dk, served to the FE so
# it can resolve ref → display text locally instead of round-tripping. Built
# once on first request.
_dk_words_flat: _SingletonCache[dict[str, str]] = _SingletonCache()


def get_qpc_cache():
    return _qpc.get()


def set_qpc_cache(data: dict) -> None:
    _qpc.set(data)


def get_dk_cache():
    return _dk.get()


def set_dk_cache(data: dict) -> None:
    _dk.set(data)


def get_dk_words_flat_cache():
    return _dk_words_flat.get()


def set_dk_words_flat_cache(data: dict[str, str]) -> None:
    _dk_words_flat.set(data)


# Surah info lite
_surah_info_lite: _SingletonCache[dict] = _SingletonCache()


def get_surah_info_lite_cache():
    return _surah_info_lite.get()


def set_surah_info_lite_cache(data: dict) -> None:
    _surah_info_lite.set(data)


# ---------------------------------------------------------------------------
# Public reciters list — the only high-frequency, anonymous, concurrent path.
# ``all_public_reciters()`` rebuilds the WHOLE catalog model (from DB via
# ``catalog_service.snapshot()``) + the state JOIN per call; cache the
# materialized list keyed on the monotonic ``db_seq`` so ANY committed write
# (any DB mutation bumps db_seq) transparently invalidates it — no explicit
# per-mutation hooks to keep in sync. Single-worker gthread: a benign race
# just recomputes for one seq.
# ---------------------------------------------------------------------------

import threading as _threading

_public_reciters_lock = _threading.Lock()
_public_reciters: "tuple[int, bool, list] | None" = None


def get_public_reciters_cache(db_seq: int, include_everyayah: bool = False):
    """Return the cached list iff it was computed at ``db_seq``, else None."""
    with _public_reciters_lock:
        if (
            _public_reciters is not None
            and _public_reciters[0] == db_seq
            and _public_reciters[1] == include_everyayah
        ):
            return _public_reciters[2]
    return None


def set_public_reciters_cache(db_seq: int, value: list, include_everyayah: bool = False) -> None:
    global _public_reciters
    with _public_reciters_lock:
        _public_reciters = (db_seq, include_everyayah, value)


def invalidate_public_reciters_cache() -> None:
    global _public_reciters
    with _public_reciters_lock:
        _public_reciters = None


# ---------------------------------------------------------------------------
# Catalog snapshot — the full ReciterCatalog rebuild from the SQLite DB
# through pydantic. Recomputed per request on /api/static/catalog.json,
# public detail / admin-view pages, and activity-feed descriptor lookups;
# ~38 ms today but ~300 ms at 10x deliveries and ~700 ms at 100x
# (single-threaded → compounds across concurrent visitors on the single
# worker). Cache the built model keyed on db_seq so the rebuild is paid
# once per write generation. Returned INSTANCE is shared — all
# catalog_service.snapshot() consumers are read-only (verified); never mutate it.
# ---------------------------------------------------------------------------

_catalog_snapshot_lock = _threading.Lock()
_catalog_snapshot: "tuple[int, object] | None" = None


def get_catalog_snapshot_cache(db_seq: int):
    """Return the cached ReciterCatalog iff built at ``db_seq``, else None."""
    with _catalog_snapshot_lock:
        if _catalog_snapshot is not None and _catalog_snapshot[0] == db_seq:
            return _catalog_snapshot[1]
    return None


def set_catalog_snapshot_cache(db_seq: int, value: object) -> None:
    global _catalog_snapshot
    with _catalog_snapshot_lock:
        _catalog_snapshot = (db_seq, value)


def invalidate_catalog_snapshot_cache() -> None:
    global _catalog_snapshot
    with _catalog_snapshot_lock:
        _catalog_snapshot = None


# Serialized ``/api/static/catalog.json`` bytes, keyed on db_seq. The snapshot
# model is already db_seq-cached, but ``model_dump(mode="json", by_alias=True)``
# + ``orjson.dumps`` still ran per request on the single worker (the TS reciter
# dropdown + dashboard forms all hit this). Cache the final bytes so a warm
# request is a dict-keyed handback. Self-invalidating via db_seq (any committed
# write bumps it), same pattern as ``_public_reciters`` / ``_admin_users``.
_catalog_json_bytes_lock = _threading.Lock()
_catalog_json_bytes: "tuple[int, bytes] | None" = None


def get_catalog_json_bytes_cache(db_seq: int):
    """Return cached catalog.json bytes iff serialized at ``db_seq``, else None."""
    with _catalog_json_bytes_lock:
        if _catalog_json_bytes is not None and _catalog_json_bytes[0] == db_seq:
            return _catalog_json_bytes[1]
    return None


def set_catalog_json_bytes_cache(db_seq: int, value: bytes) -> None:
    global _catalog_json_bytes
    with _catalog_json_bytes_lock:
        _catalog_json_bytes = (db_seq, value)


# ---------------------------------------------------------------------------
# Admin Users list — assembled read model (5 GROUP-BY aggregations merged in
# Python). Maintainer/owner-only and low-frequency, but the assembly touches
# transitions/requests/claims, so cache the built payload keyed on db_seq
# exactly like public_reciters: ANY committed write (role grants, claims,
# transitions, the hourly visitor flush) bumps db_seq → transparent
# invalidation, no per-mutation hook. The detail endpoint is lazy/bounded and
# NOT cached.
# ---------------------------------------------------------------------------

_admin_users_lock = _threading.Lock()
_admin_users: "tuple[int, object] | None" = None


def get_admin_users_cache(db_seq: int):
    """Return the cached admin-users payload iff built at ``db_seq``, else None."""
    with _admin_users_lock:
        if _admin_users is not None and _admin_users[0] == db_seq:
            return _admin_users[1]
    return None


def set_admin_users_cache(db_seq: int, value: object) -> None:
    global _admin_users
    with _admin_users_lock:
        _admin_users = (db_seq, value)


def invalidate_admin_users_cache() -> None:
    global _admin_users
    with _admin_users_lock:
        _admin_users = None


# ---------------------------------------------------------------------------
# Admin Requests tab — catalog-joined base rows per (db_seq, status). The join
# (delivery → reciter name + riwayah/style + conflict scan) is the expensive
# part; cache it keyed on db_seq so it's paid once per write generation. The
# per-caller overlay (viewed flag + requester redaction + unviewed count) is
# cheap and applied live on top, so the cache stays caller-agnostic. Keyed by
# status because the four facets are read independently; entries from older
# db_seqs are pruned on write so the dict stays bounded by the live generation.
# ---------------------------------------------------------------------------

_admin_requests_lock = _threading.Lock()
_admin_requests: "dict[tuple[int, str], object]" = {}


def get_admin_requests_cache(db_seq: int, status: str):
    """Return cached base rows for ``(db_seq, status)``, else None."""
    with _admin_requests_lock:
        return _admin_requests.get((db_seq, status))


def set_admin_requests_cache(db_seq: int, status: str, value: object) -> None:
    with _admin_requests_lock:
        for k in [k for k in _admin_requests if k[0] != db_seq]:
            _admin_requests.pop(k, None)
        _admin_requests[(db_seq, status)] = value


def invalidate_admin_requests_cache() -> None:
    with _admin_requests_lock:
        _admin_requests.clear()


# ---------------------------------------------------------------------------
# Capability matrix — the resolved (registry default ⊕ permission_overrides)
# grant map, keyed by db_seq. Any committed write bumps db_seq, so an override
# toggle transparently invalidates this on the next read (no explicit hook).
# A single tuple suffices: the matrix is process-global, not per-caller.
# ---------------------------------------------------------------------------

_capability_matrix_lock = _threading.Lock()
_capability_matrix: "tuple[int, object] | None" = None


def get_capability_matrix_cache(db_seq: int):
    """Return the cached capability matrix iff built at ``db_seq``, else None."""
    with _capability_matrix_lock:
        if _capability_matrix is not None and _capability_matrix[0] == db_seq:
            return _capability_matrix[1]
    return None


def set_capability_matrix_cache(db_seq: int, value: object) -> None:
    global _capability_matrix
    with _capability_matrix_lock:
        _capability_matrix = (db_seq, value)


def invalidate_capability_matrix_cache() -> None:
    global _capability_matrix
    with _capability_matrix_lock:
        _capability_matrix = None


# ---------------------------------------------------------------------------
# Automation config — the owner's AutomationConfig parsed from the single-row
# blob. Keyed on db_seq like the capability matrix: any committed config write
# bumps db_seq and this invalidates transparently on the next read.
# ---------------------------------------------------------------------------

_automation_config_lock = _threading.Lock()
_automation_config: "tuple[int, object] | None" = None


def get_automation_config_cache(db_seq: int):
    """Return the cached AutomationConfig iff parsed at ``db_seq``, else None."""
    with _automation_config_lock:
        if _automation_config is not None and _automation_config[0] == db_seq:
            return _automation_config[1]
    return None


def set_automation_config_cache(db_seq: int, value: object) -> None:
    global _automation_config
    with _automation_config_lock:
        _automation_config = (db_seq, value)


def invalidate_automation_config_cache() -> None:
    global _automation_config
    with _automation_config_lock:
        _automation_config = None


# ---------------------------------------------------------------------------
# In-flight HF Jobs — short TTL wrapper around ``huggingface_hub.list_jobs()``.
#
# The Releases tab polls in-flight state every 30 s; ``list_jobs()`` is
# rate-limited and ~200-400 ms per call. Cache the filtered result for 5 s so
# the poll + any same-page re-render share the same fetch, then expire so
# state catches up if no mutation invalidates first. Explicit invalidation
# fires on every ``hf_publish.launch`` / ``cut_release.launch`` and inside
# the ``complete()`` handlers so a just-launched job (or a just-terminated
# one) shows up in the next call regardless of TTL.
#
# The user-facing status route reads via ``list_in_flight_jobs(block=False)``
# (stale-while-revalidate): a lapsed TTL serves the last-known value through
# ``get_in_flight_jobs_cache_stale`` and refreshes in the background, so the
# page never blocks on the network call. The automation reconciler keeps the
# blocking ``block=True`` read — it must see accurate live state.
#
# Keyed by the ``kinds`` tuple so a call for ``("hf_publish", "cut_release")``
# doesn't collide with a future call filtered to a single kind.
# ---------------------------------------------------------------------------


_jobs_in_flight_lock = _threading.Lock()
_jobs_in_flight: "tuple[float, tuple[str, ...], list[dict]] | None" = None
_JOBS_IN_FLIGHT_TTL_S = 5.0


def get_in_flight_jobs_cache(kinds: tuple[str, ...]) -> list[dict] | None:
    """Return cached list iff stamped < TTL ago AND for the same ``kinds``."""
    with _jobs_in_flight_lock:
        if _jobs_in_flight is None:
            return None
        stamped_at, cached_kinds, value = _jobs_in_flight
        if cached_kinds != kinds:
            return None
        if _time.time() - stamped_at > _JOBS_IN_FLIGHT_TTL_S:
            return None
        return value


def get_in_flight_jobs_cache_stale(kinds: tuple[str, ...]) -> list[dict] | None:
    """Return the kinds-matched cached list ignoring the TTL, else None.

    Backs the stale-while-revalidate read on the user-facing releases-status
    route: when the TTL has lapsed we still serve the last-known value
    immediately and refresh in the background, rather than blocking on the HF
    ``list_jobs()`` network call. Returns None only when nothing has ever been
    cached for these ``kinds``."""
    with _jobs_in_flight_lock:
        if _jobs_in_flight is None:
            return None
        _stamped_at, cached_kinds, value = _jobs_in_flight
        if cached_kinds != kinds:
            return None
        return value


def set_in_flight_jobs_cache(kinds: tuple[str, ...], value: list[dict]) -> None:
    global _jobs_in_flight
    with _jobs_in_flight_lock:
        _jobs_in_flight = (_time.time(), kinds, value)


def invalidate_in_flight_jobs_cache() -> None:
    """Drop the cached list. Called by ``jobs.{hf_publish,cut_release}.launch``
    and by their ``complete()`` handlers so the FE sees the new state on the
    very next fetch (instead of waiting up to ~5 s for the TTL)."""
    global _jobs_in_flight
    with _jobs_in_flight_lock:
        _jobs_in_flight = None


# ---------------------------------------------------------------------------
# Unified all-jobs aggregation (admin Jobs tab) — TTL cache.
#
# ``jobs.registry.list_all_jobs()`` walks the bucket job-record store (every
# kind, every reciter) + merges live HF jobs. That's many small reads, and the
# Jobs tab polls ~every 10 s, so a short wall-clock TTL absorbs the poll + page
# re-renders. Invalidated alongside the in-flight cache at every launch /
# complete / cancel so a freshly recorded job shows up immediately.
# ---------------------------------------------------------------------------

_all_jobs_lock = _threading.Lock()
_all_jobs: "tuple[float, list[dict]] | None" = None
_ALL_JOBS_TTL_S = 8.0


def get_all_jobs_cache() -> list[dict] | None:
    """Return the cached unified job list iff stamped < TTL ago."""
    with _all_jobs_lock:
        if _all_jobs is None:
            return None
        stamped_at, value = _all_jobs
        if _time.time() - stamped_at > _ALL_JOBS_TTL_S:
            return None
        return value


def set_all_jobs_cache(value: list[dict]) -> None:
    global _all_jobs
    with _all_jobs_lock:
        _all_jobs = (_time.time(), value)


def invalidate_all_jobs_cache() -> None:
    """Drop the cached unified job list (launch / complete / cancel sites)."""
    global _all_jobs
    with _all_jobs_lock:
        _all_jobs = None


# ---------------------------------------------------------------------------
# Editions (multi-riwayah)
# ---------------------------------------------------------------------------
# Keyed on the SDK riwayah slug, never the Inspector slug, so one edition's
# script can never be served for another. Immutable for the life of the
# process: the data comes from a pinned ``qua-domain`` wheel, not the bucket,
# so there is no invalidation hook — a new pin means a new image.
#
# At most four editions exist, so these are unbounded by slug but tiny:
# a word map is ~77k entries, a font ~0.9 MB.

_edition_word_map: _KeyedCache[dict[str, str]] = _KeyedCache()
_edition_word_counts: _KeyedCache[dict[tuple[int, int], int]] = _KeyedCache()
_edition_projection: _KeyedCache[object] = _KeyedCache()
_edition_font: _KeyedCache[tuple[bytes, object]] = _KeyedCache()
_edition_tables: _KeyedCache[dict[str, object]] = _KeyedCache()
#: Serialised ``/api/static/quran-refs.json`` body + its 12-char digest, per
#: edition. Built from the caches above, but kept separately because the digest
#: is the asset's ETag and cache-buster — recomputing it would change nothing
#: yet cost a 3 MB serialise per request.
_edition_refs_payload: _KeyedCache[tuple[bytes, str]] = _KeyedCache()


def get_edition_word_map(riwayah: str) -> dict[str, str] | None:
    return _edition_word_map.get(riwayah)


def set_edition_word_map(riwayah: str, value: dict[str, str]) -> None:
    _edition_word_map.set(riwayah, value)


def get_edition_word_counts(riwayah: str) -> dict[tuple[int, int], int] | None:
    return _edition_word_counts.get(riwayah)


def set_edition_word_counts(riwayah: str, value: dict[tuple[int, int], int]) -> None:
    _edition_word_counts.set(riwayah, value)


def get_edition_projection(riwayah: str):
    return _edition_projection.get(riwayah)


def set_edition_projection(riwayah: str, value) -> None:
    _edition_projection.set(riwayah, value)


def get_edition_font(riwayah: str) -> tuple[bytes, object] | None:
    return _edition_font.get(riwayah)


def set_edition_font(riwayah: str, value: tuple[bytes, object]) -> None:
    _edition_font.set(riwayah, value)


def get_edition_tables(riwayah: str) -> dict[str, object] | None:
    return _edition_tables.get(riwayah)


def set_edition_tables(riwayah: str, value: dict[str, object]) -> None:
    _edition_tables.set(riwayah, value)


def get_edition_refs_payload(riwayah: str) -> tuple[bytes, str] | None:
    return _edition_refs_payload.get(riwayah)


def set_edition_refs_payload(riwayah: str, value: tuple[bytes, str]) -> None:
    _edition_refs_payload.set(riwayah, value)


def clear_edition_refs_payloads() -> None:
    """Drop only the serialised refs bundles, keeping the maps they were built
    from. Used by the test-only ``quran_refs.reset_cache``."""
    _edition_refs_payload.clear()


def clear_edition_caches() -> None:
    """Drop the edition-derived caches held *here*.

    Callers almost always want ``services.reference.editions.clear_caches()``
    instead: the accessor keeps its own ``lru_cache`` layer (the imported
    module, metadata, stop signs, special texts) which this function cannot
    reach without importing it back.
    """
    _edition_word_map.clear()
    _edition_word_counts.clear()
    _edition_projection.clear()
    _edition_font.clear()
    _edition_tables.clear()
    _edition_refs_payload.clear()
