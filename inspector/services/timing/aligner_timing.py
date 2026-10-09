"""One audio chapter's stored segment times brought up to date on the aligner.

``POST /api/v1/extraction/timing`` receives the chapter's detailed.json entries, their
bucket audio and the times stored last; it keeps every time whose segment (uid, ref, span)
is unchanged, times the rest with the neural timing head, and returns the chapter's times
(and, when asked, its shards) as base64 Brotli files written here as is. Calls for the same
chapter are serialised (:func:`chapter_lock`) so a timestamps run and a post-save re-time
never interleave their read and write of one times file.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from collections import defaultdict

from services.admin.align_pipeline import params as aligner_params
from services.storage import storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend, resolve_bucket_repo

log = logging.getLogger("inspector")

_ROUTE = "/api/v1/extraction/timing"
#: A long chapter decodes and times in about a minute on the GPU; the CPU fallback takes
#: several times that.
_READ_TIMEOUT_S = 3600

_locks: dict[tuple[str, int], threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


class TimingCallError(RuntimeError):
    """The aligner refused or failed one chapter's timing."""


def ts_engine() -> str:
    """Where timing runs: ``aligner`` (neural head, stored segment times; default) or
    ``space`` (the MFA batch timing Space, timestamps runs only)."""
    return (os.environ.get("INSPECTOR_TS_ENGINE") or "aligner").strip().lower()


def enabled() -> bool:
    """Whether segment times are stored at align and kept current after edits."""
    return ts_engine() == "aligner" and bool(aligner_params.extraction_secret())


def chapter_lock(slug: str, chapter: int) -> threading.Lock:
    with _locks_guard:
        return _locks[(slug, int(chapter))]


def read_detailed(slug: str) -> dict:
    return json.loads(get_backend().read_bytes(storage_paths.detailed_path(slug)))


def entries_by_chapter(detailed: dict) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for entry in detailed.get("entries", []):
        out[int(str(entry["ref"]).split(":")[0])].append(entry)
    return out


def audio_category(detailed: dict) -> str:
    source = str((detailed.get("_meta") or {}).get("audio_source", ""))
    by_ayah = source.startswith("by_ayah") or any(
        ":" in str(e["ref"]) for e in detailed.get("entries", [])
    )
    return "by_ayah_audio" if by_ayah else "by_surah_audio"


def read_times(slug: str, chapter: int) -> bytes | None:
    try:
        return get_backend().read_bytes(storage_paths.timing_path_br(slug, chapter))
    except StorageNotFound:
        return None


def time_chapter(
    slug: str,
    chapter: int,
    entries: list[dict],
    *,
    riwayah: str,
    category: str,
    full: bool = False,
    shards: bool = False,
) -> dict:
    """Bring ``chapter``'s stored times up to date and write them; returns the aligner's
    reply (``timed``/``kept``/``failed``, ``failed_segments``, ``model``, ``shards``)."""
    with chapter_lock(slug, chapter):
        times = read_times(slug, chapter)
        reply = _post(
            {
                "slug": slug,
                "chapter": chapter,
                "riwayah": riwayah,
                "audio_category": category,
                "entries": entries,
                "audio_refs": {str(e["ref"]): _audio_ref(slug, e["ref"]) for e in entries},
                "times": base64.b64encode(times).decode() if times else None,
                "full": full,
                "shards": shards,
            },
            chapter,
        )
        get_backend().write_bytes_atomic(
            storage_paths.timing_path_br(slug, chapter), base64.b64decode(reply["times"])
        )
    return reply


def retime(slug: str, chapters: list[int] | None = None, *, full: bool = False) -> dict:
    """Times alone for ``chapters`` (all when ``None``) of ``slug``'s current detailed.json;
    returns ``{chapter: reply}``. A chapter that no longer exists is skipped."""
    from services.reference.delivery_edition import sdk_riwayah_for

    detailed = read_detailed(slug)
    by_chapter = entries_by_chapter(detailed)
    riwayah, category = sdk_riwayah_for(slug), audio_category(detailed)
    wanted = sorted(by_chapter) if chapters is None else [c for c in chapters if c in by_chapter]
    out = {}
    for chapter in wanted:
        out[chapter] = time_chapter(
            slug, chapter, by_chapter[chapter], riwayah=riwayah, category=category, full=full
        )
        log.info(
            "[timing %s] ch%s: timed %s, kept %s, failed %s",
            slug,
            chapter,
            out[chapter]["timed"],
            out[chapter]["kept"],
            out[chapter]["failed"],
        )
    return out


def _audio_ref(slug: str, ref) -> str:
    return f"hf://buckets/{resolve_bucket_repo()}/reciters/{slug}/audio/{ref}.mp3"


def _post(body: dict, chapter: int) -> dict:
    import requests

    headers = {
        "Authorization": f"Bearer {aligner_params.hf_token()}",
        "X-Extraction-Secret": aligner_params.extraction_secret(),
    }
    resp = requests.post(
        aligner_params.aligner_url() + _ROUTE,
        json=body,
        headers=headers,
        timeout=(60, _READ_TIMEOUT_S),
    )
    if resp.status_code // 100 != 2:
        raise TimingCallError(f"ch{chapter}: aligner {resp.status_code}: {resp.text[:300]}")
    return resp.json()
