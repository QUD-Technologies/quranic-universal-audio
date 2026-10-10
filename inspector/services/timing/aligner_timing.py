"""One audio chapter's stored segment times brought up to date on the aligner.

``POST /api/v1/extraction/timing`` receives the chapter's detailed.json entries, their
bucket audio and the times stored last; it keeps every time whose segment (uid, ref, span)
is unchanged, times the rest with the neural timing head, and returns the chapter's times
(and, when asked, its shards) as base64 Brotli files written here as is. Each chapter mp3's
frame index (``audio_frames/<chapter>.bin``) lets the aligner read only the frames a few
segments need; one it builds while decoding a whole file comes back and is stored. Calls for the same
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
    """``slug``'s detailed.json with every segment's uid, derived as the read path does."""
    from domain.identity import backfill_entries_uids

    detailed = json.loads(get_backend().read_bytes(storage_paths.detailed_path(slug)))
    backfill_entries_uids(detailed.get("entries", []))
    return detailed


def entries_by_chapter(detailed: dict) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    for entry in detailed.get("entries", []):
        out[int(str(entry["ref"]).split(":")[0])].append(entry)
    return out


def chapters_of(slug: str) -> list[int]:
    return sorted(entries_by_chapter(read_detailed(slug)))


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
    *,
    riwayah: str,
    full: bool = False,
    shards: bool = False,
    delivery_lazim_ms: list[float] | None = None,
) -> dict | None:
    """Bring ``chapter``'s stored times up to date with its current segments and write them;
    returns the aligner's reply (``timed``/``kept``/``failed``, ``failed_segments``,
    ``model``, ``shards``, ``lazim_ms``), or ``None`` when the chapter has no segments any
    more. ``delivery_lazim_ms`` (:func:`delivery_lazim`) is the basis of the shards' variant
    picks. The segments are read under the chapter's lock, so a later save's re-time always
    runs after this one and writes last."""
    with chapter_lock(slug, chapter):
        detailed = read_detailed(slug)
        entries = entries_by_chapter(detailed).get(int(chapter))
        if not entries:
            return None
        times = read_times(slug, chapter)
        reply = _post(
            {
                "slug": slug,
                "chapter": chapter,
                "riwayah": riwayah,
                "audio_category": audio_category(detailed),
                "entries": entries,
                "audio_refs": {str(e["ref"]): _audio_ref(slug, e["ref"]) for e in entries},
                "frame_refs": {
                    str(e["ref"]): _frames_ref(slug, e["ref"])
                    for e in entries
                    if str(e["ref"]).isdigit()
                },
                "times": base64.b64encode(times).decode() if times else None,
                "full": full,
                "shards": shards,
                "delivery_lazim_ms": delivery_lazim_ms,
            },
            chapter,
        )
        get_backend().write_bytes_atomic(
            storage_paths.timing_path_br(slug, chapter), base64.b64decode(reply["times"])
        )
        _store_frames(slug, reply.get("frames") or {})
    return reply


def retime(slug: str, chapters: list[int] | None = None, *, full: bool = False) -> dict:
    """Times alone for ``chapters`` (all when ``None``) of ``slug``; returns
    ``{chapter: reply}``. A chapter that no longer exists is skipped."""
    from services.reference.delivery_edition import sdk_riwayah_for

    riwayah = sdk_riwayah_for(slug)
    out = {}
    for chapter in chapters_of(slug) if chapters is None else sorted(chapters):
        reply = time_chapter(slug, chapter, riwayah=riwayah, full=full)
        if reply is None:
            continue
        out[chapter] = reply
        log.info(
            "[timing %s] ch%s: timed %s, kept %s, failed %s",
            slug, chapter, reply["timed"], reply["kept"], reply["failed"],
        )  # fmt: skip
    return out


def delivery_lazim(slug: str, replies: dict[int, dict]) -> list[float]:
    """Every madd lāzim length of ``slug``: from ``replies`` (chapter → aligner reply) where
    a chapter was just timed, else from its stored times."""
    from services.timing.word_times import read_doc

    out: list[float] = []
    for chapter in chapters_of(slug):
        if chapter in replies:
            out.extend(replies[chapter].get("lazim_ms") or [])
            continue
        doc = read_doc(slug, chapter) or {}
        for held in (doc.get("segments") or {}).values():
            if held.get("status") == "ok":
                out.extend((held.get("faces") or {}).get("lazim") or [])
    return out


def _audio_ref(slug: str, ref) -> str:
    return f"hf://buckets/{resolve_bucket_repo()}/reciters/{slug}/audio/{ref}.mp3"


def _frames_ref(slug: str, ref) -> str:
    return f"hf://buckets/{resolve_bucket_repo()}/{storage_paths.audio_frames_path(slug, ref)}"


def _store_frames(slug: str, frames: dict[str, str]) -> None:
    """Store the frame indexes the aligner built; a failed write only costs a whole decode."""
    for ref, data in frames.items():
        try:
            get_backend().write_bytes_atomic(
                storage_paths.audio_frames_path(slug, ref), base64.b64decode(data)
            )
        except Exception:
            log.exception("[timing %s] storing the frame index of %s failed", slug, ref)


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
