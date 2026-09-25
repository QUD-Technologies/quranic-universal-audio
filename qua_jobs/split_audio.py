#!/usr/bin/env python3
"""Cut aligned source files into per-chapter mp3s — the align pipeline's split step.

Runs as a CPU HF Job launched by ``services/admin/align_pipeline/stage_split.py``
once the aligner has placed every surah inside each slot file. Reads
``staging/<slug>/<run_id>/split_plan.json``::

    {"chapters": {"2": [[201, start_ms, end_ms], [202, start_ms, end_ms]], ...},
     "slots": [201, 202, ...]}

Each chapter is one or more *pieces* (a surah uploaded in parts), each a window
of ``reciters/<slug>/audio/<slot>.mp3``; they are encoded end to end into the
canonical ``audio/<ch>.mp3`` and its ``peaks/<ch>.json.gz``. The Inspector
rebases the chapter's timestamps with the same windows, so cut and timings agree
by construction.

Writes ``staging/<slug>/<run_id>/split.json`` (``cuts`` + ``failures``). Every
slot file is deleted only when every cut succeeded, so a retry can cut again.
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


def _cut_one(slug: str, chapter: int, pieces: list[list[int]], channels: int) -> dict:
    reciter = _root() / "reciters" / slug
    mp3 = reciter / "audio" / f"{chapter}.mp3"
    peaks = reciter / "peaks" / f"{chapter}.json.gz"
    if mp3.is_file() and peaks.is_file():
        return {
            "bytes": mp3.stat().st_size,
            "duration_ms": audio_io.probe_duration_ms(mp3),
            "pieces": len(pieces),
            "skipped": True,
        }
    windows = [(reciter / "audio" / f"{int(s)}.mp3", int(a), int(b)) for s, a, b in pieces]
    with tempfile.TemporaryDirectory(prefix=f"split_{chapter}_") as tmp:
        encoded = Path(tmp) / f"{chapter}.mp3"
        audio_io.encode_pieces(windows, encoded, channels)
        blob, duration_ms = audio_io.bake_peaks(encoded)
        audio_io.atomic_write_bytes(peaks, blob)
        audio_io.atomic_write(mp3, encoded)
        return {
            "bytes": encoded.stat().st_size,
            "duration_ms": duration_ms,
            "pieces": len(pieces),
            "skipped": False,
        }


def split_all(slug: str, plan: dict, workers: int) -> tuple[dict[str, dict], dict[str, str]]:
    cuts: dict[str, dict] = {}
    failures: dict[str, str] = {}
    audio = _root() / "reciters" / slug / "audio"
    channels_of: dict[int, int] = {}
    jobs = []
    for key, pieces in (plan.get("chapters") or {}).items():
        chapter = int(key)
        slots = {int(p[0]) for p in pieces}
        if not (audio / f"{chapter}.mp3").is_file():
            missing = sorted(s for s in slots if not (audio / f"{s}.mp3").is_file())
            if missing:
                failures[key] = f"source slot {missing[0]} is missing from the bucket"
                continue
        for s in slots:
            if s not in channels_of and (audio / f"{s}.mp3").is_file():
                channels_of[s] = audio_io.probe_channels(audio / f"{s}.mp3")
        channels = max((channels_of.get(s, 1) for s in slots), default=1)
        jobs.append((chapter, pieces, channels))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="split") as pool:
        futures = {pool.submit(_cut_one, slug, *job): job[0] for job in jobs}
        for future in as_completed(futures):
            chapter = futures[future]
            try:
                cuts[str(chapter)] = future.result()
                log.info("chapter %d: cut", chapter)
            except Exception as exc:  # noqa: BLE001 — recorded per chapter
                failures[str(chapter)] = f"{type(exc).__name__}: {exc}"
                log.error("chapter %d: FAILED %s", chapter, failures[str(chapter)])
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
    total = len(plan.get("chapters") or {})
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
    for slot in plan.get("slots") or []:
        (_root() / "reciters" / slug / "audio" / f"{int(slot)}.mp3").unlink(missing_ok=True)
    log.info("%s: %d chapter(s) cut, source slots removed", slug, len(cuts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
