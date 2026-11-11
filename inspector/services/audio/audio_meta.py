"""Per-chapter audio metadata, read from ``catalog/audio_manifest/<slug>.json``.

The sidecar carries one entry per chapter::

    "<key>": {"url": ..., "size_bytes": ..., "duration_sec": ...,
              "bitrate_kbps": ..., "bitrate_mode": "vbr" | "cbr"}

Keys are ``"1"``…``"114"`` for by_surah deliveries and ``"<surah>:<ayah>"``
for by_ayah. The sidecar is written by the offline probe
(``scripts/probe_audio_meta.py``) and is the single source of truth for both
VBR routing decisions and the chapter→URL reverse lookup that powers the
audio-proxy short-circuit, the segment-clip allowlist, and the peaks route's
chapter→URL enumeration.

No Flask imports — pure data access.
"""

from __future__ import annotations

import logging

from services.storage import cache, storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend

logger = logging.getLogger(__name__)


def _load_sidecar(slug: str) -> dict | None:
    """Read + cache the audio manifest sidecar for ``slug``.

    Populates two LRU-bounded cache slots on first read:
    - ``_audio_manifest`` — the full sidecar doc.
    - ``_audio_manifest_url_index`` — derived ``{url: chapter_key}`` map for
      O(1) ``chapter_for_url`` / ``chapter_meta_for_url`` reverse lookups
      (the peaks fan-out previously did a linear scan per URL).
    """
    cached = cache.get_audio_manifest_cache(slug)
    if cached is not None:
        return cached
    try:
        doc = get_backend().read_json(storage_paths.audio_manifest_path(slug))
    except StorageNotFound:
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("audio_meta: failed to read sidecar for %s: %s", slug, e)
        return None
    if not isinstance(doc, dict):
        return None
    cache.set_audio_manifest_cache(slug, doc)
    # Build the URL → chapter-key inverse index alongside the sidecar so
    # both lookups stay in lockstep. One O(N_chapters) walk at load time
    # replaces O(N_chapters) per URL reverse lookup on every peaks request.
    chapters = doc.get("chapters") or {}
    inverse: dict[str, str] = {}
    if isinstance(chapters, dict):
        for k, entry in chapters.items():
            if not isinstance(entry, dict):
                continue
            u = entry.get("url")
            if isinstance(u, str) and u:
                inverse[u] = k
    cache.set_audio_manifest_url_index_cache(slug, inverse)
    return doc


def _chapters(slug: str) -> dict:
    doc = _load_sidecar(slug)
    if not doc:
        return {}
    chapters = doc.get("chapters")
    return chapters if isinstance(chapters, dict) else {}


def manifest_checksum(chapters: dict) -> str:
    """``_meta.checksum``: a stable digest of the sorted ``(key, url)`` pairs, so
    re-writing the same chapters produces the same checksum."""
    import hashlib

    src = "".join(f"{k}={chapters[k]['url']};" for k in sorted(chapters)).encode("utf-8")
    return hashlib.sha256(src).hexdigest()[:16]


def manifest_chapters(slug: str) -> dict:
    """The sidecar's raw ``chapters`` map (``{}`` when there is no sidecar)."""
    return _chapters(slug)


def manifest_sources(slug: str) -> list[dict]:
    """The sidecar's ``sources`` — files still awaiting surah detection."""
    doc = _load_sidecar(slug) or {}
    sources = doc.get("sources")
    return sources if isinstance(sources, list) else []


def chapter_meta(reciter: str, chapter: int | str) -> dict | None:
    """Return the sidecar entry for ``(reciter, chapter)``, or None."""
    entry = _chapters(reciter).get(str(chapter))
    return entry if isinstance(entry, dict) else None


def chapter_meta_for_url(reciter: str, url: str) -> dict | None:
    """Reverse lookup: chapter entry by URL. None when unknown."""
    key = chapter_for_url(reciter, url)
    if key is None:
        return None
    entry = _chapters(reciter).get(key)
    return entry if isinstance(entry, dict) else None


def _is_vbr_entry(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    mode = entry.get("bitrate_mode")
    return isinstance(mode, str) and mode.lower() == "vbr"


def is_vbr(reciter: str, chapter: int | str) -> bool:
    """True iff the chapter's sidecar entry has ``bitrate_mode == "vbr"``."""
    return _is_vbr_entry(chapter_meta(reciter, chapter))


def is_vbr_for_url(reciter: str, url: str) -> bool:
    """URL-keyed sibling of :func:`is_vbr` — used by callers without a chapter
    number (notably the peaks endpoint, where the request body is URL-keyed).
    """
    return _is_vbr_entry(chapter_meta_for_url(reciter, url))


def vbr_chapters_for_reciter(reciter: str) -> list[int]:
    """Sorted list of chapter numbers known VBR for ``reciter``.

    Shipped to the frontend so cross-chapter accordion prefetch picks the
    right transport (clip URL vs chapter URL) without a per-chapter probe
    round-trip. Non-integer keys (by_ayah ``"<s>:<a>"``) are skipped — only
    by_surah deliveries expose VBR chapters today.
    """
    out: list[int] = []
    for k, entry in _chapters(reciter).items():
        if not _is_vbr_entry(entry):
            continue
        try:
            out.append(int(k))
        except (TypeError, ValueError):
            continue
    out.sort()
    return out


def chapter_bitrate_kbps_for_reciter(reciter: str) -> dict[str, int]:
    """{chapter -> bitrate_kbps} for chapters that are CBR with a positive kbps.

    Shipped to the frontend so the segments-tab audio-warmup util can compute
    a chapter byte offset from a seg's ``time_start`` and warm a 64 KB Range
    ahead of the user's play click. Absence from this map (VBR, missing kbps,
    unknown mode) is the no-op signal — keep the wire payload sparse.

    Non-integer keys (by_ayah ``"<s>:<a>"``) are skipped — only by_surah
    deliveries expose chapter-level bitrate today.

    Keys are string-typed for orjson compatibility (it rejects int dict keys);
    the FE parses them back to int.
    """
    out: dict[str, int] = {}
    for k, entry in _chapters(reciter).items():
        if not isinstance(entry, dict):
            continue
        mode = (entry.get("bitrate_mode") or "").lower()
        if mode != "cbr":
            continue
        kbps = entry.get("bitrate_kbps")
        if not isinstance(kbps, (int, float)) or kbps <= 0:
            continue
        try:
            ch_int = int(k)
        except (TypeError, ValueError):
            continue
        out[str(ch_int)] = int(kbps)
    return out


def chapter_for_url(reciter: str, url: str) -> str | None:
    """Reverse-lookup the chapter key for a URL — used by the audio-proxy
    short-circuit. Returns the raw sidecar key (``"1"``..``"114"`` or
    ``"<surah>:<ayah>"``) or None when the URL is unknown.

    O(1) via the inverse index populated alongside the sidecar (see
    ``_load_sidecar``).
    """
    _load_sidecar(reciter)  # ensure both caches are populated
    idx = cache.get_audio_manifest_url_index_cache(reciter)
    if not idx:
        return None
    return idx.get(url)


def chapter_urls(reciter: str) -> dict[str, str]:
    """Full chapter-key → URL map. Drives the ``/peaks`` route's chapter→URL
    enumeration. Empty dict when the sidecar is missing.
    """
    out: dict[str, str] = {}
    for k, entry in _chapters(reciter).items():
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            out[k] = entry["url"]
    return out


def _stage_for_test(slug: str, doc: dict) -> None:
    """Test helper — install a sidecar doc + derived URL index in one call.

    Replaces the pre-refactor ``audio_meta._SIDECAR_CACHE[slug] = doc``
    pattern that tests used directly. Builds the URL → chapter-key inverse
    index from ``doc`` so reverse lookups work without round-tripping
    through ``_load_sidecar``.
    """
    cache.set_audio_manifest_cache(slug, doc)
    inverse: dict[str, str] = {}
    chapters = doc.get("chapters") or {}
    if isinstance(chapters, dict):
        for k, entry in chapters.items():
            if isinstance(entry, dict):
                u = entry.get("url")
                if isinstance(u, str) and u:
                    inverse[u] = k
    cache.set_audio_manifest_url_index_cache(slug, inverse)


def _clear_for_test() -> None:
    """Test helper — drop every cached sidecar + URL index. Mirrors the
    pre-refactor ``audio_meta._SIDECAR_CACHE.clear()`` pattern."""
    # Access the underlying _KeyedCache slots through cache.py setters by
    # popping every staged slug. Tests stage a small set; iterate the dict.
    am = getattr(cache, "_audio_manifest", None)
    idx = getattr(cache, "_audio_manifest_url_index", None)
    if am is not None and hasattr(am, "clear"):
        am.clear()
    if idx is not None and hasattr(idx, "clear"):
        idx.clear()


def chapter_numbers(reciter: str) -> list[int]:
    """Sorted unique chapter (surah) numbers derived from the sidecar keys.

    Works for both delivery shapes — by_surah keys are ``"1"``..``"114"`` and
    map 1:1 to a surah; by_ayah keys are ``"<surah>:<ayah>"`` and collapse to
    their surah component. Used by the TS manifest builder so the released
    chapter set is derived from cached sidecar state instead of a per-slug
    ``list_dir("reciters/<slug>/timestamps")`` bucket walk.

    The invariant this leans on: ``state == released`` ⇒ every
    audio chapter has timestamps published. Enforced by the
    ``under_review → released`` publish transition, which fires only after the
    timestamps job succeeds. A reciter that violates the invariant degrades to
    a shard 404 on click; the manifest stays consistent with itself.
    """
    out: set[int] = set()
    for k in _chapters(reciter).keys():
        try:
            out.add(int(str(k).split(":", 1)[0]))
        except (TypeError, ValueError):
            continue
    return sorted(out)
