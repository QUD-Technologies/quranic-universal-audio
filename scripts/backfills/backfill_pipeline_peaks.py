#!/usr/bin/env python3
"""One-shot per-op peaks backfill for pipeline edits in a reciter's history.

The alignment pipeline (``.local/extraction/segments/post_passes.py``)
writes ``waqf_sakt`` and ``strip_specials`` operations into
``edit_history.jsonl``, and ``audio_persist.write_edit_history_peaks``
appends the matching slim records into ``edit_history_peaks.jsonl`` in
the same extraction pass. Inspector reads those records directly — no
runtime backfill, no auto_detect hook.

This script is the **catch-up tool** for reciters whose
``edit_history_peaks.jsonl`` got out of sync with ``edit_history.jsonl``
— e.g. a partial migration, a manual mutation of the history file, or a
legacy reciter that pre-dates the offline writer.

Migration #5 invariants assumed (no legacy heuristics):
  * Every snapshot has ``chapter`` and ``audio_url`` stamped.
  * Every record on disk uses the slim ``peaks_b64`` + ``bps`` shape.

If those invariants aren't met for a given slug, fix the source data
(via ``.local/extraction/scripts/migrate_wip5_in_place.py`` on the slug
dir, or by re-extracting). Don't keep adding fallbacks here.

Usage:
    python3 scripts/backfills/backfill_pipeline_peaks.py \\
        --slug <slug> [--slug <slug2> ...] [--bucket prod|dev] [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger("backfill_pipeline_peaks")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

# Mirror of ``.local/extraction/segments/audio_persist.py::_encode_peaks_b64``
# (and ``inspector/services/audio/peaks_slim.py``).
_PEAKS_INT8_SCALE = 127
_PEAKS_SLIM_BPS = 10


def _encode_peaks_b64(peaks: list[list[float]]) -> tuple[str, int]:
    """Encode a ``list[[mn, mx], ...]`` slice as int8-quantised b64."""
    import numpy as np  # noqa: PLC0415

    arr = np.asarray(peaks, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError(f"_encode_peaks_b64: expected shape (N, 2), got {arr.shape}")
    i8 = np.clip(
        np.round(arr * _PEAKS_INT8_SCALE),
        -_PEAKS_INT8_SCALE,
        _PEAKS_INT8_SCALE,
    ).astype(np.int8)
    return base64.b64encode(i8.tobytes()).decode("ascii"), _PEAKS_SLIM_BPS


def _iter_pipeline_ops(records: Iterable[dict]) -> Iterable[tuple[dict, dict]]:
    """Yield ``(batch, op)`` for every pipeline op (waqf_sakt or
    strip_specials)."""
    for batch in records:
        if batch.get("record_type") == "genesis":
            continue
        is_strip = batch.get("batch_type") == "strip_specials"
        for op in batch.get("operations") or []:
            if is_strip or op.get("op_type") == "waqf_sakt":
                yield batch, op


def _resolve_op_chapter(op: dict, batch: dict) -> int | None:
    """Migration #5 contract: snapshot's ``chapter`` first, then batch's
    ``chapter`` (waqf_sakt batches stamp it). No legacy heuristics."""
    for snap in (op.get("targets_before") or []) + (op.get("targets_after") or []):
        if isinstance(snap, dict):
            ch = snap.get("chapter")
            if isinstance(ch, int) and ch > 0:
                return ch
    bch = batch.get("chapter")
    if isinstance(bch, int) and bch > 0:
        return bch
    return None


def _resolve_op_audio_url(
    op: dict,
    batch: dict,
    by_chapter: dict[int, str],
) -> str | None:
    """Snapshot's stamped ``audio_url`` first, then catalog lookup by
    resolved chapter."""
    for snap in (op.get("targets_before") or []) + (op.get("targets_after") or []):
        if isinstance(snap, dict):
            url = snap.get("audio_url")
            if isinstance(url, str) and url:
                return url
    ch = _resolve_op_chapter(op, batch)
    if ch is None:
        return None
    return by_chapter.get(ch)


def _op_time_range(op: dict) -> tuple[int, int] | None:
    """``(min start_ms, max end_ms)`` across all snapshots, or None."""
    starts: list[int] = []
    ends: list[int] = []
    for snap in (op.get("targets_before") or []) + (op.get("targets_after") or []):
        if not isinstance(snap, dict):
            continue
        t0 = snap.get("time_start")
        t1 = snap.get("time_end")
        if isinstance(t0, (int, float)) and isinstance(t1, (int, float)) and t1 > t0:
            starts.append(int(t0))
            ends.append(int(t1))
    if not starts:
        return None
    return min(starts), max(ends)


def _audio_url_by_chapter(reciter: str) -> dict[int, str]:
    """Map ``chapter -> URL`` from the bucket audio_manifest sidecar."""
    from services.audio.audio_meta import chapter_urls

    out: dict[int, str] = {}
    for key, url in chapter_urls(reciter).items():
        if ":" in str(key):
            continue
        try:
            out[int(key)] = url
        except (TypeError, ValueError):
            continue
    return out


def backfill_one_slug(reciter: str, *, dry_run: bool) -> int:
    """Compute + persist missing peaks for one reciter. Returns records
    written (0 on dry-run)."""
    from services.activity.history_query import parse_history_for_reciter
    from services.audio.peaks import compute_segment_peaks
    from services.audio.peaks_history import append_peaks_records, load_peaks_records

    records = list(parse_history_for_reciter(reciter))
    if not records:
        log.info("[%s] no edit_history records", reciter)
        return 0
    pipeline_ops = list(_iter_pipeline_ops(records))
    if not pipeline_ops:
        log.info("[%s] no pipeline ops in history", reciter)
        return 0

    already_persisted: set[str] = {
        rec.get("op_id") for rec in load_peaks_records(reciter) if isinstance(rec.get("op_id"), str)
    }
    missing = [
        (batch, op) for batch, op in pipeline_ops if op.get("op_id") not in already_persisted
    ]
    log.info(
        "[%s] %d pipeline ops total, %d already persisted, %d missing",
        reciter,
        len(pipeline_ops),
        len(already_persisted),
        len(missing),
    )
    if not missing:
        return 0

    by_chapter = _audio_url_by_chapter(reciter)
    by_batch: dict[str | None, list[dict]] = {}

    for batch, op in missing:
        op_id = op.get("op_id")
        if not isinstance(op_id, str):
            continue
        rng = _op_time_range(op)
        if rng is None:
            log.warning("[%s] op %s has no usable time range; skipping", reciter, op_id)
            continue
        url = _resolve_op_audio_url(op, batch, by_chapter)
        if not url:
            log.warning("[%s] op %s has no resolvable audio_url; skipping", reciter, op_id)
            continue
        if dry_run:
            log.info(
                "[%s] dry-run: would compute peaks for op %s at %s [%d..%d]",
                reciter,
                op_id,
                url,
                rng[0],
                rng[1],
            )
            continue
        try:
            data = compute_segment_peaks(url, rng[0], rng[1], reciter)
        except Exception:  # noqa: BLE001
            log.exception("[%s] compute_segment_peaks failed for op %s", reciter, op_id)
            continue
        if not data or not data.get("peaks"):
            log.warning("[%s] empty peaks for op %s", reciter, op_id)
            continue
        peaks_b64, bps = _encode_peaks_b64(data["peaks"])
        record = {
            "op_id": op_id,
            "url": url,
            "start_ms": rng[0],
            "end_ms": rng[1],
            "bps": bps,
            "peaks_b64": peaks_b64,
        }
        by_batch.setdefault(batch.get("batch_id"), []).append(record)

    if dry_run:
        log.info("[%s] dry-run: %d records would be appended", reciter, len(missing))
        return 0

    written = 0
    for batch_id, recs in by_batch.items():
        written += append_peaks_records(reciter, recs, batch_id=batch_id)
    log.info("[%s] wrote %d peaks record(s)", reciter, written)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--slug", action="append", required=True, help="Reciter slug. Repeat for multiple."
    )
    ap.add_argument(
        "--bucket",
        choices=sorted(_BUCKETS),
        default="prod",
        help="Bucket to operate on (default: prod).",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Identify missing ops, log them, write nothing."
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # sys.path + env BEFORE inspector imports.
    here = Path(__file__).resolve()
    repo = here.parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[args.bucket]

    total = 0
    for slug in args.slug:
        total += backfill_one_slug(slug, dry_run=args.dry_run)

    log.info(
        "total: %d record(s) written across %d slug(s)%s",
        total,
        len(args.slug),
        " (dry-run)" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
