#!/usr/bin/env python3
"""Persist a delivery's source audio to the bucket — the align pipeline's first stage.

Runs as a CPU HF Job launched by ``services/admin/align_pipeline/stage_acquire.py``.
Reads the delivery's audio manifest (``catalog/audio_manifest/<slug>.json``) and
groups its chapters by source file (``qua_shared.audio.sources``):

* a **single-chapter** source is fetched, encoded to the canonical chapter mp3
  (``qua_jobs/audio_io.encode``) and its peaks baked →
  ``reciters/<slug>/audio/<ch>.mp3`` + ``peaks/<ch>.json.gz``;
* a **combined** source (several chapters in one Drive mp3 / YouTube video) is
  fetched and encoded ONCE into its source slot ``reciters/<slug>/audio/<slot>.mp3``
  — the align stage aligns it whole and ``split_audio.py`` cuts it per chapter.

Sources run concurrently on a thread pool (one worker per vCPU, ``ACQUIRE_WORKERS``
overrides, 8 max); every step releases the GIL (socket waits, ffmpeg subprocesses).
yt-dlp fetches are capped at ``YTDLP_WORKERS`` at once, and after YouTube's first
bot-check refusal the remaining yt-dlp sources fail fast with the same reason.
Idempotent: a chapter whose mp3 + peaks exist, or a slot whose mp3 exists (or
whose chapters were already split), is skipped. Writes the report
``staging/<slug>/<run_id>/acquire.json`` and exits non-zero when any source failed.

Env:
  SLUG, RUN_ID            (required) delivery slug, align run id
  INSPECTOR_BUCKET_MOUNT  bucket mount root (default ``/data``)
  CHANNELS                optional 1|2 override; default = probe the source
  ACQUIRE_WORKERS         optional concurrent source count
  YTDLP_COOKIES           optional secret — Netscape cookies.txt for YouTube
  YTDLP_PROXY             optional proxy URL handed to yt-dlp
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.environ.get("PYTHONPATH", "/aux/code"))

from qua_jobs import audio_io  # noqa: E402
from qua_shared.audio.sources import (  # noqa: E402
    SourceGroup,
    groups_from_manifest,
    needs_ytdlp,
)

log = logging.getLogger("acquire_audio")

#: Beyond this the source host, not the flavor, is the limit — and each worker
#: holds a raw + an encoded copy of its file in the job's ephemeral disk.
MAX_WORKERS = 8

#: yt-dlp fetches in flight at once: one account hammering YouTube from a
#: datacenter IP is what trips its bot check.
YTDLP_WORKERS = 2

_log_lock = threading.Lock()
_ytdlp_gate = threading.Semaphore(YTDLP_WORKERS)
#: The first bot-check refusal; later yt-dlp sources fail fast with it.
_bot_blocked: list[str] = []


def _fetch(url: str, dest: Path) -> Path:
    if not needs_ytdlp(url):
        return audio_io.fetch(url, dest)
    with _ytdlp_gate:
        if _bot_blocked:
            raise audio_io.BotCheckError(f"skipped — {_bot_blocked[0]}")
        try:
            return audio_io.fetch(url, dest)
        except audio_io.BotCheckError as exc:
            _bot_blocked.append(str(exc))
            raise


def _bucket_root() -> Path:
    return Path(os.environ.get("INSPECTOR_BUCKET_MOUNT", "/data"))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _worker_count(sources: int) -> int:
    override = os.environ.get("ACQUIRE_WORKERS", "").strip()
    if override.isdigit() and int(override) > 0:
        return min(int(override), sources)
    return max(1, min(os.cpu_count() or 1, MAX_WORKERS, sources))


def read_groups(slug: str, run_id: str) -> list[SourceGroup]:
    """The run's frozen grouping (``groups.json``, written by the Inspector when
    the run starts) — the slot numbers must match what it aligns and splits.
    Falls back to grouping the manifest for a standalone run."""
    frozen = _bucket_root() / "staging" / slug / run_id / "groups.json"
    if frozen.is_file():
        raw = json.loads(frozen.read_text(encoding="utf-8"))["groups"]
        return [
            SourceGroup(url=g["url"], chapters=tuple(g["chapters"]), slot=g.get("slot"))
            for g in raw
        ]
    path = _bucket_root() / "catalog" / "audio_manifest" / f"{slug}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    groups = groups_from_manifest(doc.get("chapters") or {}, doc.get("sources") or [])
    if not groups:
        raise ValueError(f"{slug}: audio manifest lists no chapters or sources")
    return groups


# ---------------------------------------------------------------------------
# One source
# ---------------------------------------------------------------------------


def _paths(slug: str, number: int) -> tuple[Path, Path]:
    reciter = _bucket_root() / "reciters" / slug
    return reciter / "audio" / f"{number}.mp3", reciter / "peaks" / f"{number}.json.gz"


def _already_done(slug: str, group: SourceGroup) -> bool:
    if group.chapters and all(all(p.is_file() for p in _paths(slug, ch)) for ch in group.chapters):
        return True  # every chapter persisted (a combined one: already split)
    return group.combined and _paths(slug, group.item)[0].is_file()


def acquire_group(slug: str, group: SourceGroup, channels_override: int | None) -> dict:
    """Fetch + encode one source (and bake peaks for a single chapter)."""
    mp3_dest, peaks_dest = _paths(slug, group.item)
    if _already_done(slug, group):
        log.info("%s: already persisted, skipped", _label(group))
        return {"url": group.url, "skipped": True}
    with tempfile.TemporaryDirectory(prefix=f"acq_{group.item}_") as tmp:
        work = Path(tmp)
        raw = _fetch(group.url, work / "src.bin")
        channels = channels_override or audio_io.probe_channels(raw)
        encoded = work / f"{group.item}.mp3"
        audio_io.encode(raw, encoded, channels)
        outcome = {
            "url": group.url,
            "skipped": False,
            "bytes": encoded.stat().st_size,
            "channels": channels,
        }
        if group.combined:
            outcome["duration_ms"] = audio_io.probe_duration_ms(encoded)
        else:
            blob, outcome["duration_ms"] = audio_io.bake_peaks(encoded)
            audio_io.atomic_write_bytes(peaks_dest, blob)
        audio_io.atomic_write(mp3_dest, encoded)
    return outcome


def _label(group: SourceGroup) -> str:
    if not group.combined:
        return f"chapter {group.chapters[0]}"
    if group.detect:
        return f"slot {group.slot} (surahs detected after aligning)"
    return f"slot {group.slot} (chapters {group.chapters[0]}-{group.chapters[-1]})"


def acquire_all(
    slug: str, groups: list[SourceGroup], channels_override: int | None, workers: int
) -> tuple[dict[str, dict], dict[str, dict], dict[str, str]]:
    """Returns ``(chapter outcomes, slot outcomes, failures)``; failures are keyed
    by the chapter (single) or ``"slot <n>"`` (combined)."""
    chapters: dict[str, dict] = {}
    slots: dict[str, dict] = {}
    failures: dict[str, str] = {}
    total = len(groups)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="acq") as pool:
        futures = {pool.submit(acquire_group, slug, g, channels_override): g for g in groups}
        for future in as_completed(futures):
            group = futures[future]
            key = f"slot {group.slot}" if group.combined else str(group.chapters[0])
            try:
                outcome = future.result()
                outcome["chapters"] = list(group.chapters)
                if group.combined:
                    slots[str(group.slot)] = outcome
                else:
                    chapters[key] = outcome
                message = f"{_label(group)}: ok ({group.url})"
            except Exception as exc:  # noqa: BLE001 — recorded per source, run continues
                failures[key] = f"{type(exc).__name__}: {exc}"
                message = f"{_label(group)}: FAILED {failures[key]}"
            with _log_lock:
                done = len(chapters) + len(slots) + len(failures)
                log.info("[%d/%d] %s", done, total, message)
    return chapters, slots, failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    slug = os.environ.get("SLUG", "").strip()
    run_id = os.environ.get("RUN_ID", "").strip()
    if not slug or not run_id:
        log.error("SLUG and RUN_ID are required")
        return 2
    channels_env = os.environ.get("CHANNELS", "").strip()
    channels_override = int(channels_env) if channels_env in ("1", "2") else None

    groups = read_groups(slug, run_id)
    workers = _worker_count(len(groups))
    combined = sum(1 for g in groups if g.combined)
    log.info(
        "%s: %d source(s) (%d combined) on %d worker(s) (run %s)",
        slug,
        len(groups),
        combined,
        workers,
        run_id,
    )
    chapters, slots, failures = acquire_all(slug, groups, channels_override, workers)
    report = {
        "slug": slug,
        "run_id": run_id,
        "created_at": _now(),
        "chapters": {k: chapters[k] for k in sorted(chapters, key=int)},
        "sources": {k: slots[k] for k in sorted(slots, key=int)},
        "failures": failures,
    }
    audio_io.atomic_write_bytes(
        _bucket_root() / "staging" / slug / run_id / "acquire.json",
        json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"),
    )
    if failures:
        log.error("%s: %d source(s) failed: %s", slug, len(failures), sorted(failures))
        return 1
    log.info("%s: all %d source(s) persisted", slug, len(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
