"""Data loading functions for timestamps, segments, audio URLs, and reference data.

All data is loaded once and cached via ``services.cache``. Functions here never
import Flask -- they return plain dicts/lists.

Per-reciter reads (``load_seg_verses``, ``load_detailed``, ``load_probe_v2``,
``load_hidden_pause``, ``load_missed_waqf``, ``load_false_split``,
``load_unmarked_wasl``)
go through the storage backend via ``services.data_dir`` — no direct
filesystem access to ``RECITATION_SEGMENTS_PATH``. Static reference data
(qpc_hafs, surah_info, digital_khatt) still lives in the image at
``INSPECTOR_DATA_DIR`` and is read directly.

The four reference accessors — ``get_dk_words_flat``, ``get_word_counts``,
``get_single_word_verses``, ``word_has_stop`` — take a riwayah, defaulting to
Hafs. The Hafs branch reads the bundled files exactly as before (so every
existing call site is byte-identical); every other edition is served from
``services.reference.editions``, which owns the optional ``qua_domain``
dependency. Nothing here falls back to Hafs for a non-Hafs riwayah: it raises.
"""

import threading

from adapters.detailed_json import (
    load_entries_from_bytes as _load_detailed_entries_from_bytes,
)
from config import (
    DK_SCRIPT_PATH,
    SURAH_INFO_PATH,
)
from constants import STOP_SIGNS
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.storage import cache, data_dir, static_refs

_detailed_locks: dict[str, threading.Lock] = {}
_detailed_locks_guard = threading.Lock()


def _detailed_lock(reciter: str) -> threading.Lock:
    with _detailed_locks_guard:
        lock = _detailed_locks.get(reciter)
        if lock is None:
            lock = threading.Lock()
            _detailed_locks[reciter] = lock
        return lock


# ---------------------------------------------------------------------------
# QPC / Digital Khatt
# ---------------------------------------------------------------------------


def load_qpc() -> dict[str, dict]:
    """Load and cache qpc_hafs.json.

    Byte resolution (local image → bucket) lives in
    ``services.storage.static_refs.load_qpc_bytes`` — on the deployed Space the
    image's ``.gz`` is an LFS pointer, so the real bytes come from the bucket.
    """
    cached = cache.get_qpc_cache()
    if cached is not None:
        return cached
    import orjson

    raw = static_refs.load_qpc_bytes()
    data = orjson.loads(raw) if raw else {}
    cache.set_qpc_cache(data)
    return data


def load_dk() -> dict[str, dict]:
    """Load and cache digital_khatt_v2_script.json."""
    cached = cache.get_dk_cache()
    if cached is not None:
        return cached
    if DK_SCRIPT_PATH.exists():
        import orjson

        data = orjson.loads(DK_SCRIPT_PATH.read_bytes())
    else:
        data = {}
    cache.set_dk_cache(data)
    return data


def get_dk_words_flat(riwayah: str = DEFAULT_SDK_RIWAYAH) -> dict[str, str]:
    """Flat ``"surah:ayah:word" -> text`` word map for one edition.

    Promoted off per-request payloads to the immutable
    ``/api/static/quran-refs.json`` asset (see ``services/reference/quran_refs``).
    Still consumed in-process by save / auto-split / validation.

    Hafs comes from ``digital_khatt_v2_script.json`` — end-of-verse markers
    (``۝``) stripped, since ``dkTextForRef`` walks bounded by verse word counts
    and never indexes them. Every other edition comes from its packaged index,
    which carries no marker entries to begin with.
    """
    if riwayah != DEFAULT_SDK_RIWAYAH:
        from services.reference import editions

        return editions.word_map(riwayah)
    cached = cache.get_dk_words_flat_cache()
    if cached is not None:
        return cached
    flat: dict[str, str] = {}
    for loc, entry in load_dk().items():
        text = entry.get("text") if isinstance(entry, dict) else None
        if text and not text.startswith("۝"):
            flat[loc] = text
    cache.set_dk_words_flat_cache(flat)
    return flat


# Timestamps tab read path now lives in `services/timestamps.py` — see that
# module for the manifest + per-chapter shard cache that replaced the
# eager `discover_ts_reciters` / `load_timestamps` loaders.


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


def resolve_pad(meta: dict) -> tuple[int, int, int]:
    """Resolve VAD pad fields from ``_meta`` with alias-on-read.

    Returns ``(pad_left_ms, pad_right_ms, min_silence_floor_ms)``.

    For files written before asymmetric pad landed, ``pad_ms`` is
    treated as both left and right; ``min_silence_floor_ms`` defaults
    to 0 (no daylight guarantee was enforced pre-feature).
    """
    legacy = int(meta.get("pad_ms", 0))
    pad_left = int(meta.get("pad_left_ms", legacy))
    pad_right = int(meta.get("pad_right_ms", legacy))
    floor = int(meta.get("min_silence_floor_ms", 0))
    return pad_left, pad_right, floor


def load_seg_verses(reciter: str) -> tuple[dict, int, int, int]:
    """Load segments.json verse data for boundary mismatch checking.

    Returns ``(verses, pad_left_ms, pad_right_ms, min_silence_floor_ms)``.
    Cached.
    """
    cached = cache.get_seg_verses_cache(reciter)
    if cached is not None:
        return cached
    doc = data_dir.read_segments_doc(reciter)
    if doc is None:
        return {}, 0, 0, 0
    meta = doc.get("_meta", {})
    pad_left, pad_right, floor = resolve_pad(meta)
    verses = {k: v for k, v in doc.items() if k != "_meta"}
    result = (verses, pad_left, pad_right, floor)
    cache.set_seg_verses_cache(reciter, result)
    return result


def load_detailed(reciter: str) -> list[dict]:
    """Load and cache all entries from a reciter's detailed.json."""
    cached = cache.get_seg_cache(reciter)
    if cached is not None:
        return cached
    with _detailed_lock(reciter):
        cached = cache.get_seg_cache(reciter)
        if cached is not None:
            return cached
        raw = data_dir.read_detailed_bytes(reciter)
        if raw is None:
            # Remember the absence too: without it every caller re-reads a
            # missing file, a bucket round-trip per request, on exactly the
            # slugs the Reviews drawer sweeps. Marked `absent` so it lapses on
            # its own: `invalidate_seg_caches` drops it when a save, a discard
            # or the auto-detect reconciler notices the file, but the reconciler
            # only looks at deliveries still awaiting alignment — a promote run
            # against any other slug would otherwise 404 it until restart, with
            # no way in, because the editor cannot load to save.
            cache.set_seg_cache(reciter, [], absent=True)
            return []
        meta, entries = _load_detailed_entries_from_bytes(raw)
        if meta:
            cache.set_seg_meta(reciter, meta)
        cache.set_seg_cache(reciter, entries)
        # Fallback: if detailed.json had no _meta, try segments.json
        if not cache.get_seg_meta(reciter):
            seg_doc = data_dir.read_segments_doc(reciter)
            if seg_doc and "_meta" in seg_doc:
                cache.set_seg_meta(reciter, seg_doc["_meta"])
        return entries


def seg_meta(reciter: str) -> dict:
    """The delivery's ``detailed.json`` ``_meta``, loading the file if needed.

    ``cache.get_seg_meta`` answers ``{}`` for both "the file carries no meta"
    and "nothing has read the file in this process yet". A caller that reads
    the cache directly therefore gets a silent pass on a cold process — which
    is precisely when a cross-check against ``_meta`` matters. Going through
    :func:`load_detailed` makes the answer mean what it says; the load is
    cached, and every Segments path warms it anyway.
    """
    meta = cache.get_seg_meta(reciter)
    if meta or cache.get_seg_cache(reciter) is not None:
        # Entries cached with no meta is a real answer (a file written before
        # the field existed), not a cold process — do not re-read for it.
        return meta
    load_detailed(reciter)
    return cache.get_seg_meta(reciter)


def load_probe_v2(reciter: str) -> tuple[set[str], dict | None]:
    """Load ``low_confidence_v2.json`` sidecar for *reciter*.

    Returns ``(failed_uid_set, meta_dict)``. When the sidecar is absent
    returns ``(set(), None)`` and caches the empty result so repeated
    lookups don't re-stat the filesystem. The sidecar is the source of
    truth for the *Low Confidence v2* validation category and is never
    written from the Inspector — it's emitted offline by the segments-stage
    MFA probe (qua-aligner-offline).
    """
    cached = cache.get_seg_probe_v2(reciter)
    if cached is not None:
        return cached
    doc = data_dir.read_low_confidence_doc(reciter)
    if doc is None:
        result: tuple[set[str], dict | None] = (set(), None)
        cache.set_seg_probe_v2(reciter, result)
        return result
    failures = doc.get("failures") or []
    meta = doc.get("_meta") or None
    result = (set(failures), meta)
    cache.set_seg_probe_v2(reciter, result)
    return result


def load_pipeline_meta(reciter: str) -> dict | None:
    """Load ``pipeline_meta.json`` for *reciter* (immutable post-extraction).

    Returns the validated ``PipelineMeta`` dict, or ``None`` if the sidecar
    is missing. The cache is **never invalidated by save** — the sidecar
    records extraction-time facts that no user edit can change.

    Callers that depend on a field (e.g. ``deleted_basmala_chapters``) should
    hard-fail on ``None`` rather than silently substituting an empty set;
    missing sidecar means the backfill script hasn't run for this reciter.
    """
    from qua_shared.schemas import PipelineMeta

    cached = cache.get_seg_pipeline_meta(reciter)
    if cached is not None:
        return cached
    doc = data_dir.read_pipeline_meta_doc(reciter)
    if doc is None:
        return None
    try:
        # ``exclude_none`` so an optional field the sidecar doesn't carry
        # (e.g. ``riwayah`` on a pre-multi-riwayah extraction) stays absent
        # rather than being served as an explicit ``null``.
        validated = PipelineMeta.model_validate(doc).model_dump(mode="json", exclude_none=True)
    except Exception:  # noqa: BLE001 — a malformed/forward sidecar must not 500 the validation panel
        # The sidecar is a hot read on /api/seg/validate; a single un-migrated
        # or forward-compat field would otherwise raise ValidationError →
        # HTTP 500 for the reciter's entire validation view. Degrade to the
        # raw doc (consumers read declared keys like deleted_basmala_chapters)
        # and surface the drift to the nightly bucket validator instead.
        import logging

        logging.getLogger(__name__).warning(
            "[%s] pipeline_meta failed PipelineMeta validation; serving raw doc", reciter
        )
        validated = doc
    cache.set_seg_pipeline_meta(reciter, validated)
    return validated


def load_auto_split(reciter: str) -> tuple[dict[str, dict], dict | None]:
    """Load ``auto_split_v1.json`` sidecar for *reciter*.

    Returns ``(by_uid_map, meta_dict)`` where each value in ``by_uid_map`` is
    ``{"cursors": [int, ...], "refs": [str, ...], "kind": "cross_verse" |
    "repetition"}``. Empty dict + None when the sidecar is absent — that
    signals to the FE that every Auto Split candidate should fall back to
    the plain Split button (manual single-cursor placement). The sidecar is
    emitted offline by ``qua_shared/auto_split_precompute.py``; the
    Inspector never writes it.
    """
    cached = cache.get_seg_auto_split(reciter)
    if cached is not None:
        return cached
    doc = data_dir.read_auto_split_doc(reciter)
    if doc is None:
        result: tuple[dict[str, dict], dict | None] = ({}, None)
        cache.set_seg_auto_split(reciter, result)
        return result
    by_uid = doc.get("by_uid") or {}
    meta = doc.get("_meta") or None
    if not isinstance(by_uid, dict):
        by_uid = {}
    result = (by_uid, meta)
    cache.set_seg_auto_split(reciter, result)
    return result


def _load_by_uid_sidecar(
    reciter: str,
    read_doc,
    get_cached,
    set_cached,
) -> tuple[dict[str, dict], dict | None]:
    """Shared ``{"_meta", "by_uid"}`` sidecar loader with the per-reciter cache
    pattern. Absent sidecar → ``({}, None)``, cached so lookups don't re-stat."""
    cached = get_cached(reciter)
    if cached is not None:
        return cached
    doc = read_doc(reciter)
    by_uid = doc.get("by_uid") if doc else None
    meta = doc.get("_meta") if doc else None
    result: tuple[dict[str, dict], dict | None] = (
        by_uid if isinstance(by_uid, dict) else {},
        meta if isinstance(meta, dict) else None,
    )
    set_cached(reciter, result)
    return result


def load_hidden_pause(reciter: str) -> tuple[dict[str, dict], dict | None]:
    """Load ``hidden_pause_v1.json`` — offline re-segmentation cuts inside a
    segment, keyed by ``segment_uid``. Never written by the Inspector."""
    return _load_by_uid_sidecar(
        reciter,
        data_dir.read_hidden_pause_doc,
        cache.get_seg_hidden_pause,
        cache.set_seg_hidden_pause,
    )


def load_missed_waqf(reciter: str) -> tuple[dict[str, dict], dict | None]:
    """Load ``missed_waqf_v1.json`` — cursors inside a segment where an offline
    phoneme + silence detector heard the reciter stop, keyed by
    ``segment_uid``. Never written by the Inspector."""
    return _load_by_uid_sidecar(
        reciter,
        data_dir.read_missed_waqf_doc,
        cache.get_seg_missed_waqf,
        cache.set_seg_missed_waqf,
    )


def load_false_split(reciter: str) -> tuple[dict[str, dict], dict | None]:
    """Load ``false_split_v1.json`` — offline evidence of continuous speech
    across a segment's end, keyed by ``segment_uid``. Never written by the
    Inspector."""
    return _load_by_uid_sidecar(
        reciter,
        data_dir.read_false_split_doc,
        cache.get_seg_false_split,
        cache.set_seg_false_split,
    )


def load_unmarked_wasl(reciter: str) -> tuple[dict[str, dict], dict | None]:
    """Load ``unmarked_wasl_v1.json`` — offline evidence that every arm read
    through a verse-to-verse join the delivery never marked ``is_wasl``, keyed
    by the left segment's ``segment_uid``. Never written by the Inspector."""
    return _load_by_uid_sidecar(
        reciter,
        data_dir.read_unmarked_wasl_doc,
        cache.get_seg_unmarked_wasl,
        cache.set_seg_unmarked_wasl,
    )


# Audio URL maps remain cached via `cache._audio_url`, but the only
# remaining caller is `routes/audio_metadata.py` (Audio tab), which now
# loads them inline. The Timestamps tab's old `load_audio_urls` flow is
# gone — `services/timestamps.py` inlines the per-chapter URL slice into
# each shard's `_meta` instead.


# ---------------------------------------------------------------------------
# Word counts and surah info
# ---------------------------------------------------------------------------


def get_word_counts(riwayah: str = DEFAULT_SDK_RIWAYAH) -> dict[tuple[int, int], int]:
    """``{(surah, ayah): word_count}`` under this edition's counting profile.

    Not a reindex of the Hafs map: Warsh/Qalun renumber 50 of the 114 surahs
    (6,214 ayahs against Hafs's 6,236), so a Hafs verse key can be out of range
    there entirely.

    The Hafs branch loads ``surah_info.json`` and also primes
    ``cache.set_single_word_verses_cache`` with the derived
    ``{(surah, ayah): wc == 1}`` set so classifier / save callers don't rebuild
    it per call.
    """
    if riwayah != DEFAULT_SDK_RIWAYAH:
        from services.reference import editions

        return editions.word_counts(riwayah)
    cached = cache.get_word_counts_cache()
    if cached is not None:
        return cached
    wc: dict[tuple[int, int], int] = {}
    if SURAH_INFO_PATH.exists():
        import orjson

        si = orjson.loads(SURAH_INFO_PATH.read_bytes())
        for surah_str, data in si.items():
            for v in data["verses"]:
                wc[(int(surah_str), v["verse"])] = v["num_words"]
    cache.set_word_counts_cache(wc)
    cache.set_single_word_verses_cache({k for k, v in wc.items() if v == 1})
    return wc


def get_single_word_verses(riwayah: str = DEFAULT_SDK_RIWAYAH) -> set[tuple[int, int]]:
    """``(surah, ayah)`` keys whose verse is exactly one word long.

    Hafs has 28, Warsh/Qalun 3 — the muqattaat openings are standalone verses
    in the Kufan count and swallowed into longer verses in the Medinan one. The
    edition-aware set lives in ``services.reference.edition_tables``; this
    accessor is the Hafs one the save/stamping paths already hold.

    Derived from ``get_word_counts()`` once at first read; cached for the
    process lifetime alongside word_counts (both immutable post-boot).
    """
    if riwayah != DEFAULT_SDK_RIWAYAH:
        from services.reference import edition_tables

        return set(edition_tables.single_word_verses(riwayah))
    cached = cache.get_single_word_verses_cache()
    if cached is not None:
        return cached
    # Force compute via get_word_counts (which primes the swv cache).
    get_word_counts()
    return cache.get_single_word_verses_cache() or set()


def load_surah_info_lite() -> dict:
    """Load lightweight surah metadata: number -> {name_en, name_ar, num_verses}."""
    cached = cache.get_surah_info_lite_cache()
    if cached is not None:
        return cached
    import orjson

    raw = orjson.loads(SURAH_INFO_PATH.read_bytes())
    result = {}
    for num, info in raw.items():
        result[num] = {
            "name_en": info.get("name_en", ""),
            "name_ar": info.get("name_ar", ""),
            "num_verses": info["num_verses"],
        }
    cache.set_surah_info_lite_cache(result)
    return result


def word_has_stop(surah: int, ayah: int, word_num: int, riwayah: str = DEFAULT_SDK_RIWAYAH) -> bool:
    """True iff this edition writes a waqf stop sign on the given word.

    Hafs reads ``qpc_hafs.json`` against ``constants.STOP_SIGNS`` (four signs,
    deliberately excluding the paired stop and the sakt). Other editions read
    their own script against their own observed inventory — Warsh and Qalun
    carry only U+06D6, on 9,948 words.
    """
    if riwayah != DEFAULT_SDK_RIWAYAH:
        from services.reference import editions

        text = editions.word_map(riwayah).get(f"{surah}:{ayah}:{word_num}", "")
        return bool(editions.stop_signs(riwayah) & set(text))
    qpc = load_qpc()
    entry = qpc.get(f"{surah}:{ayah}:{word_num}")
    if not entry:
        return False
    return bool(STOP_SIGNS & set(entry.get("text", "")))
