#!/usr/bin/env python3
"""Cut combined source files into per-chapter mp3s — the align pipeline's split step.

Runs as a CPU HF Job launched by ``services/admin/align_pipeline/stage_split.py``
once the aligner has placed every chapter inside each combined source. Reads
``staging/<slug>/<run_id>/split_plan.json``::

    {"slots": {"901": {"chapters": {"1": [start_ms, end_ms], "2": [...]}}}}

and, for every chapter, encodes that window of ``reciters/<slug>/audio/<slot>.mp3``
to the canonical ``audio/<ch>.mp3`` and bakes ``peaks/<ch>.json.gz``. Chapter
times the Inspector publishes are rebased by the same ``start_ms`` it planned, so
the cut and the timestamps agree by construction.

Writes ``staging/<slug>/<run_id>/split.json`` (``cuts`` + ``failures``). The slot
files are deleted only when every cut succeeded, so a retry can cut again.
Idempotent per chapter: an already-persisted chapter is re-measured, not re-cut.

Env: SLUG, RUN_ID (required); INSPECTOR_BUCKET_MOUNT (default ``/data``);
SPLIT_WORKERS (default one per vCPU, 8 max).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, os.environ.get("PYTHONPATH", "/aux/code"))

from qua_jobs import audio_io  # noqa: E402

log = logging.getLogger("split_audio")

MAX_WORKERS = 8


def _root() -> Path:
    return Path(os.environ.get("INSPECTOR_BUCKET_MOUNT", "/data"))


def _cut_one(slug: str, slot: int, chapter: int, window: list[int], channels: int) -> dict:
    reciter = _root() / "reciters" / slug
    mp3 = reciter / "audio" / f"{chapter}.mp3"
    peaks = reciter / "peaks" / f"{chapter}.json.gz"
    start_ms, end_ms = int(window[0]), int(window[1])
    if mp3.is_file() and peaks.is_file():
        return {
            "slot": slot,
            "offset_ms": start_ms,
            "bytes": mp3.stat().st_size,
            "duration_ms": audio_io.probe_duration_ms(mp3),
            "skipped": True,
        }
    source = reciter / "audio" / f"{slot}.mp3"
    with tempfile.TemporaryDirectory(prefix=f"split_{chapter}_") as tmp:
        encoded = Path(tmp) / f"{chapter}.mp3"
        audio_io.encode(source, encoded, channels, start_ms=start_ms, end_ms=end_ms)
        blob, duration_ms = audio_io.bake_peaks(encoded)
        audio_io.atomic_write_bytes(peaks, blob)
        audio_io.atomic_write(mp3, encoded)
        return {
            "slot": slot,
            "offset_ms": start_ms,
            "bytes": encoded.stat().st_size,
            "duration_ms": duration_ms,
            "skipped": False,
        }


def split_all(slug: str, plan: dict, workers: int) -> tuple[dict[str, dict], dict[str, str]]:
    cuts: dict[str, dict] = {}
    failures: dict[str, str] = {}
    reciter = _root() / "reciters" / slug
    jobs = []
    for slot_key, spec in (plan.get("slots") or {}).items():
        slot = int(slot_key)
        source = reciter / "audio" / f"{slot}.mp3"
        chapters = {int(k): v for k, v in (spec.get("chapters") or {}).items()}
        pending = [ch for ch in chapters if not (reciter / "audio" / f"{ch}.mp3").is_file()]
        if pending and not source.is_file():
            for ch in pending:
                failures[str(ch)] = f"source slot {slot} is missing from the bucket"
            continue
        channels = audio_io.probe_channels(source) if source.is_file() else 1
        jobs.extend((slot, ch, window, channels) for ch, window in chapters.items())
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="split") as pool:
        futures = {pool.submit(_cut_one, slug, *job): job for job in jobs}
        for future in as_completed(futures):
            slot, chapter = futures[future][0], futures[future][1]
            try:
                cuts[str(chapter)] = future.result()
                log.info("chapter %d (slot %d): cut", chapter, slot)
            except Exception as exc:  # noqa: BLE001 — recorded per chapter
                failures[str(chapter)] = f"{type(exc).__name__}: {exc}"
                log.error("chapter %d (slot %d): FAILED %s", chapter, slot, failures[str(chapter)])
    return cuts, failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    slug = os.environ.get("SLUG", "").strip()
    run_id = os.environ.get("RUN_ID", "").strip()
    if not slug or not run_id:
        log.error("SLUG and RUN_ID are required")
        return 2
    staging = _root() / "staging" / slug / run_id
    plan = json.loads((staging / "split_plan.json").read_text(encoding="utf-8"))
    total = sum(len(s.get("chapters") or {}) for s in (plan.get("slots") or {}).values())
    override = os.environ.get("SPLIT_WORKERS", "").strip()
    workers = int(override) if override.isdigit() and int(override) > 0 else None
    workers = max(1, min(workers or os.cpu_count() or 1, MAX_WORKERS, total or 1))
    log.info("%s: cutting %d chapter(s) on %d worker(s) (run %s)", slug, total, workers, run_id)

    cuts, failures = split_all(slug, plan, workers)
    report = {"cuts": dict(sorted(cuts.items(), key=lambda kv: int(kv[0]))), "failures": failures}
    audio_io.atomic_write_bytes(
        staging / "split.json", json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8")
    )
    if failures:
        log.error("%s: %d chapter(s) failed", slug, len(failures))
        return 1
    for slot_key in plan.get("slots") or {}:
        (_root() / "reciters" / slug / "audio" / f"{int(slot_key)}.mp3").unlink(missing_ok=True)
    log.info("%s: %d chapter(s) cut, source slots removed", slug, len(cuts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
