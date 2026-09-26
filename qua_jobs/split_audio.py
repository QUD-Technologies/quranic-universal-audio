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

Files move through ``bucket_io`` (the Hub HTTP API on HF, never the bucket
mount: reads off the mount hung and failed mid-file while the job wrote to it,
and ffmpeg wrote truncated chapters with exit 0). Every cut's length is checked
against the plan. Every chapter is cut on every run, so a stale or truncated
chapter from an earlier run is overwritten.

Writes ``staging/<slug>/<run_id>/split.json`` (``cuts`` + ``failures``). Every
slot file is deleted only when every cut succeeded, so a retry can cut again.

Env: SLUG, RUN_ID (required); BUCKET_REPO (HTTP access; unset = files under
INSPECTOR_BUCKET_MOUNT); SPLIT_WORKERS (default one per vCPU, 8 max).
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

from qua_jobs import audio_io, bucket_io  # noqa: E402

log = logging.getLogger("split_audio")

MAX_WORKERS = 8
#: A cut whose length is off the plan by more than this is refused (encoder
#: padding and frame rounding stay well under it; a truncated read does not).
LENGTH_TOLERANCE_MS = 1500


def _audio(slug: str, number: int) -> str:
    return f"reciters/{slug}/audio/{number}.mp3"


def _expected_ms(pieces: list[list[int]]) -> int:
    return sum(int(b) - int(a) for _s, a, b in pieces)


def _check_length(chapter: int, duration_ms: int, expected_ms: int) -> None:
    if abs(duration_ms - expected_ms) > LENGTH_TOLERANCE_MS:
        raise RuntimeError(
            f"chapter {chapter} cut is {duration_ms / 1000:.1f}s, "
            f"the plan says {expected_ms / 1000:.1f}s"
        )


def _cut_one(slug: str, chapter: int, pieces: list[list[int]]) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"split_{chapter}_") as tmp:
        work = Path(tmp)
        local: dict[int, Path] = {}
        for slot in sorted({int(p[0]) for p in pieces}):
            try:
                local[slot] = bucket_io.fetch(_audio(slug, slot), work / f"src_{slot}.mp3")
            except bucket_io.MissingFile:
                raise RuntimeError(f"source slot {slot} is missing from the bucket") from None
        channels = max(audio_io.probe_channels(p) for p in local.values())
        windows = [(local[int(s)], int(a), int(b)) for s, a, b in pieces]
        encoded = work / f"{chapter}.mp3"
        audio_io.encode_pieces(windows, encoded, channels)
        blob, duration_ms = audio_io.bake_peaks(encoded)
        _check_length(chapter, duration_ms, _expected_ms(pieces))
        bucket_io.put_bytes(blob, f"reciters/{slug}/peaks/{chapter}.json.gz", work / "peaks.gz")
        bucket_io.put(encoded, _audio(slug, chapter))
        return {
            "bytes": encoded.stat().st_size,
            "duration_ms": duration_ms,
            "pieces": len(pieces),
            "skipped": False,
        }


def split_all(slug: str, plan: dict, workers: int) -> tuple[dict[str, dict], dict[str, str]]:
    cuts: dict[str, dict] = {}
    failures: dict[str, str] = {}
    jobs = [(int(key), pieces) for key, pieces in (plan.get("chapters") or {}).items()]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="split") as pool:
        futures = {pool.submit(_cut_one, slug, *job): job[0] for job in jobs}
        for future in as_completed(futures):
            chapter = futures[future]
            try:
                cuts[str(chapter)] = future.result()
                log.info("chapter %d: cut (%d ms)", chapter, cuts[str(chapter)]["duration_ms"])
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
    staging = f"staging/{slug}/{run_id}"
    with tempfile.TemporaryDirectory(prefix="split_") as tmp:
        work = Path(tmp)
        plan_path = bucket_io.fetch(f"{staging}/split_plan.json", work / "split_plan.json")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        total = len(plan.get("chapters") or {})
        override = os.environ.get("SPLIT_WORKERS", "").strip()
        workers = int(override) if override.isdigit() and int(override) > 0 else None
        workers = max(1, min(workers or os.cpu_count() or 1, MAX_WORKERS, total or 1))
        log.info("%s: cutting %d chapter(s) on %d worker(s) (run %s)", slug, total, workers, run_id)

        cuts, failures = split_all(slug, plan, workers)
        report = {
            "cuts": dict(sorted(cuts.items(), key=lambda kv: int(kv[0]))),
            "failures": failures,
        }
        bucket_io.put_bytes(
            json.dumps(report, ensure_ascii=False, indent=1).encode("utf-8"),
            f"{staging}/split.json",
            work / "split.json",
        )
    if failures:
        log.error("%s: %d chapter(s) failed", slug, len(failures))
        return 1
    bucket_io.delete([_audio(slug, int(slot)) for slot in plan.get("slots") or []])
    log.info("%s: %d chapter(s) cut, source slots removed", slug, len(cuts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
