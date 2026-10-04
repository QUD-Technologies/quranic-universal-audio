#!/usr/bin/env python3
"""Audit production segment pauses against compact v12 timestamp boundaries.

Writes a deterministic summary JSON, all observed non-wasl boundaries as JSONL,
and the high-signal subset as JSONL. Reads only; never mutates the bucket.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import brotli
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bucket"))

import _bootstrap as bucket  # noqa: E402

from qua_shared.timestamps_codec import decode_document  # noqa: E402

DEFAULT_MANIFEST = "https://qud-technologies-quranic-universal-audio.hf.space/api/ts/manifest"


def _matched_ref_to_output_key(matched_ref: str) -> str | None:
    """Segment matched_ref to its output key: '1:1:1-1:1:4' -> '1:1';
    cross-verse '37:151:3-37:152:2' kept as-is. None when not a paired ref."""
    for prefix in ("Basmala+", "Isti'adha+"):
        if matched_ref.startswith(prefix):
            matched_ref = matched_ref[len(prefix) :]
    parts = matched_ref.split("-")
    if len(parts) != 2:
        return None
    start_parts, end_parts = parts[0].split(":"), parts[1].split(":")
    if len(start_parts) != 3 or len(end_parts) != 3:
        return None
    start_sura, start_ayah = start_parts[0], start_parts[1]
    return f"{start_sura}:{start_ayah}" if start_ayah == end_parts[1] else matched_ref


def build_mfa_ref(seg: dict) -> str | None:
    """The alignable verse ref for a segment, or None to skip (empty ref, low
    confidence, or a transition segment like Amin/Takbir with no colon)."""
    matched_ref = seg.get("matched_ref", "")
    if not matched_ref or seg.get("confidence", 0) <= 0 or ":" not in matched_ref:
        return None
    return matched_ref

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--manifest-url", default=DEFAULT_MANIFEST)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--chapter", type=int, action="append", default=[])
    parser.add_argument("--source-pause-ms", type=int, default=200)
    parser.add_argument("--collapsed-gap-ms", type=int, default=100)
    parser.add_argument("--absorption-ms", type=int, default=100)
    return parser.parse_args()


def _manifest(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    raw = response.content
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def _read(fs, bucket_id: str, path: str, cache: Path | None = None) -> bytes:
    if cache is not None:
        cached = cache / path
        if cached.is_file():
            return cached.read_bytes()
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            return fs.cat_file(bucket.abs_path(bucket_id, path))
        except Exception as exc:  # noqa: BLE001 - retry transient Hub failures
            last_error = exc
            if attempt == 4:
                break
            time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _prefetch_reciter(slug: str, chapters: list[int], cache: Path) -> dict:
    from huggingface_hub import download_bucket_files, list_bucket_tree

    token = bucket.load_hf_token()
    bucket_id = bucket.BUCKETS["prod"]
    wanted = {
        f"reciters/{slug}/timestamps/{chapter}.json.br" for chapter in chapters
    }
    detailed_path = f"reciters/{slug}/detailed.json"
    downloads: list[tuple[Any, Path]] = []
    detailed_target = cache / detailed_path
    if not detailed_target.is_file():
        downloads.append((detailed_path, detailed_target))
    for item in list_bucket_tree(
        bucket_id,
        prefix=f"reciters/{slug}/timestamps",
        recursive=True,
        token=token,
    ):
        if item.type != "file" or item.path not in wanted:
            continue
        target = cache / item.path
        if not target.is_file():
            downloads.append((item, target))
    found = {str(remote.path if hasattr(remote, "path") else remote) for remote, _ in downloads}
    missing = [path for path in wanted if not (cache / path).is_file() and path not in found]
    if missing:
        return {"slug": slug, "downloaded": 0, "error": f"missing bucket files: {missing[:3]}"}
    if downloads:
        for _, target in downloads:
            target.parent.mkdir(parents=True, exist_ok=True)
        download_bucket_files(bucket_id, files=downloads, token=token)
    return {"slug": slug, "downloaded": len(downloads), "error": None}


def _chapter(entry: dict) -> int | None:
    try:
        return int(str(entry.get("ref", "")).split(":", 1)[0])
    except ValueError:
        return None


def _part_index(decoded: dict) -> dict[tuple[str, int, int], tuple[dict, dict]]:
    out = {}
    for reading in decoded["readings"]:
        for part in reading["parts"]:
            out[(part["ref"], int(part["t"][0]), int(part["t"][1]))] = (reading, part)
    return out


def _timing(reading: dict, key: str, field: str) -> dict[int, dict]:
    return {int(row[field]): row for row in reading["timing"][key]}


def _final_sound_id(word: dict, sound_times: dict[int, dict]) -> int | None:
    sound_ids = [int(one) for one in word.get("sound_ids") or [] if int(one) in sound_times]
    if not sound_ids:
        return None
    return max(
        sound_ids,
        key=lambda one: (int(sound_times[one]["end_ms"]), int(sound_times[one]["start_ms"])),
    )


def _baseline_durations(decoded: dict) -> dict[str, Counter[int]]:
    """Durations except exact sounds that end words followed by a stop boundary."""
    durations: dict[str, Counter[int]] = defaultdict(Counter)
    for reading in decoded["readings"]:
        result = reading["analysis"]["result"]
        sound_times = _timing(reading, "sounds", "sound_id")
        boundaries = {int(row["id"]): row for row in result["boundaries"]}
        excluded_sound_ids: set[int] = set()
        for word in result["words"]:
            after = boundaries.get(int(word["after_boundary_id"]))
            if after is None or after.get("state") != "stop":
                continue
            sound_id = _final_sound_id(word, sound_times)
            if sound_id is not None:
                excluded_sound_ids.add(sound_id)
        sounds = {int(row["id"]): row for row in result["sounds"]}
        for sound_id, timing in sound_times.items():
            if sound_id in excluded_sound_ids or sound_id not in sounds:
                continue
            duration = int(timing["end_ms"]) - int(timing["start_ms"])
            if duration >= 0:
                durations[str(sounds[sound_id]["token"])][duration] += 1
    return dict(durations)


def _is_variable_long_vowel(token: str) -> bool:
    return token.endswith((":", "ː"))


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))]


def _duration_stats(values: list[int]) -> dict[str, int | float | None]:
    if not values:
        return {
            "count": 0,
            "mean_ms": None,
            "median_ms": None,
            "p95_ms": None,
            "sample_stddev_ms": None,
            "min_ms": None,
            "max_ms": None,
        }
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 3),
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": _percentile(values, 0.95),
        "sample_stddev_ms": (
            round(statistics.stdev(values), 3) if len(values) >= 2 else None
        ),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def _hist_value_at(histogram: dict[int, int], ordinal: int) -> int:
    seen = 0
    for duration, count in sorted(histogram.items()):
        seen += count
        if seen >= ordinal:
            return duration
    raise ValueError("histogram ordinal exceeds population")


def _duration_histogram_stats(
    histogram: dict[int, int],
) -> dict[str, int | float | None]:
    count = sum(histogram.values())
    if count == 0:
        return _duration_stats([])
    mean = sum(duration * frequency for duration, frequency in histogram.items()) / count
    if count % 2:
        median = _hist_value_at(histogram, count // 2 + 1)
    else:
        median = (
            _hist_value_at(histogram, count // 2)
            + _hist_value_at(histogram, count // 2 + 1)
        ) / 2
    variance = (
        sum(frequency * (duration - mean) ** 2 for duration, frequency in histogram.items())
        / (count - 1)
        if count >= 2
        else None
    )
    return {
        "count": count,
        "mean_ms": round(mean, 3),
        "median_ms": round(median, 3),
        "p95_ms": _hist_value_at(histogram, math.ceil(0.95 * count)),
        "sample_stddev_ms": round(math.sqrt(variance), 3) if variance is not None else None,
        "min_ms": min(histogram),
        "max_ms": max(histogram),
    }


def _boundary_record(
    slug: str,
    chapter: int,
    left_index: int,
    left: dict,
    right_index: int,
    right: dict,
    indexed: dict,
    *,
    source_pause_ms: int,
    collapsed_gap_ms: int,
    absorption_ms: int,
) -> tuple[dict | None, str | None]:
    left_ref = _matched_ref_to_output_key(str(left.get("matched_ref", "")))
    right_ref = _matched_ref_to_output_key(str(right.get("matched_ref", "")))
    if left_ref is None or right_ref is None:
        return None, "unresolved_segment_ref"
    if left_ref != right_ref:
        return None, None
    left_start, left_end = int(left["time_start"]), int(left["time_end"])
    right_start, right_end = int(right["time_start"]), int(right["time_end"])
    source_gap = right_start - left_end
    if source_gap <= 0 or bool(left.get("is_wasl")):
        return None, None

    left_hit = indexed.get((left_ref, left_start, left_end))
    right_hit = indexed.get((right_ref, right_start, right_end))
    if left_hit is None or right_hit is None:
        return None, "part_not_found"
    left_reading, left_part = left_hit
    right_reading, right_part = right_hit
    if left_reading["id"] == right_reading["id"]:
        return None, "non_wasl_parts_share_reading"

    left_word_id = int(left_part["word_ids"][-1])
    right_word_id = int(right_part["word_ids"][0])
    left_words = _timing(left_reading, "words", "word_id")
    right_words = _timing(right_reading, "words", "word_id")
    left_word = left_words[left_word_id]
    right_word = right_words[right_word_id]
    left_word_end = int(left_word["end_ms"])
    right_word_start = int(right_word["start_ms"])
    acoustic_gap = right_word_start - left_word_end

    boundary_by_id = _timing(left_reading, "boundaries", "boundary_id")
    boundary = boundary_by_id[left_word_id + 1]
    displayed_gap = int(boundary["end_ms"]) - int(boundary["start_ms"])

    result = left_reading["analysis"]["result"]
    native_word = next(row for row in result["words"] if int(row["id"]) == left_word_id)
    sounds = {int(row["id"]): row for row in result["sounds"]}
    sound_times = _timing(left_reading, "sounds", "sound_id")
    final_sound_id = _final_sound_id(native_word, sound_times)
    if final_sound_id is None:
        return None, "left_word_has_no_timed_sound"
    final_sound = sound_times[final_sound_id]
    final_sound_start = int(final_sound["start_ms"])
    final_sound_end = int(final_sound["end_ms"])
    final_sound_past_segment = max(0, final_sound_end - max(final_sound_start, left_end))
    left_tail_overrun = left_word_end - left_end
    right_lead_delay = right_word_start - right_start
    positive_acoustic_gap = max(0, acoustic_gap)
    shard_zeroed = positive_acoustic_gap > 0 and displayed_gap == 0
    likely_collapsed = (
        source_gap >= source_pause_ms
        and 0 <= acoustic_gap <= collapsed_gap_ms
        and left_tail_overrun >= absorption_ms
        and final_sound_past_segment >= absorption_ms
    )
    collapse_likelihood = (
        "high"
        if likely_collapsed
        else (
            "medium"
            if source_gap >= source_pause_ms and 0 <= acoustic_gap <= collapsed_gap_ms
            else "none"
        )
    )
    return (
        {
            "slug": slug,
            "chapter": chapter,
            "verse_ref": left_ref,
            "left_segment_index": left_index,
            "right_segment_index": right_index,
            "left_matched_ref": left.get("matched_ref"),
            "right_matched_ref": right.get("matched_ref"),
            "left_reading_id": left_reading["id"],
            "right_reading_id": right_reading["id"],
            "left_word_id": left_word_id,
            "right_word_id": right_word_id,
            "left_word_ref": native_word["ref"],
            "left_word_text": native_word["text"],
            "source_segment_gap_ms": source_gap,
            "acoustic_word_gap_ms": acoustic_gap,
            "displayed_v12_boundary_ms": displayed_gap,
            "shard_lost_gap_ms": positive_acoustic_gap - displayed_gap,
            "left_word_end_ms": left_word_end,
            "right_word_start_ms": right_word_start,
            "left_segment_end_ms": left_end,
            "right_segment_start_ms": right_start,
            "left_word_overrun_ms": left_tail_overrun,
            "right_word_lead_delay_ms": right_lead_delay,
            "final_sound_id": final_sound_id,
            "final_sound_token": sounds[final_sound_id]["token"],
            "final_sound_start_ms": final_sound_start,
            "final_sound_end_ms": final_sound_end,
            "final_sound_duration_ms": final_sound_end - final_sound_start,
            "final_sound_past_segment_end_ms": final_sound_past_segment,
            "shard_zeroed_positive_acoustic_gap": shard_zeroed,
            "collapse_likelihood": collapse_likelihood,
            "decoder_only_repair_ms": positive_acoustic_gap if shard_zeroed else 0,
            "full_source_gap_recoverable_from_v12": acoustic_gap == source_gap,
        },
        None,
    )


def _audit_reciter(
    slug: str,
    chapters: list[int],
    *,
    source_pause_ms: int,
    collapsed_gap_ms: int,
    absorption_ms: int,
    cache: Path | None,
) -> dict:
    from huggingface_hub import HfFileSystem

    fs = None if cache is not None else HfFileSystem(token=bucket.load_hf_token())
    bucket_id = bucket.BUCKETS["prod"]
    detailed = json.loads(_read(fs, bucket_id, f"reciters/{slug}/detailed.json", cache))
    entries = {_chapter(row): row for row in detailed.get("entries") or []}
    rows, errors, coverage_anomalies = [], [], []
    baseline_durations: dict[str, Counter[int]] = defaultdict(Counter)
    for chapter in chapters:
        entry = entries.get(chapter)
        if entry is None:
            errors.append({"chapter": chapter, "error": "detailed_chapter_not_found"})
            continue
        try:
            stored = json.loads(
                brotli.decompress(
                    _read(
                        fs,
                        bucket_id,
                        f"reciters/{slug}/timestamps/{chapter}.json.br",
                        cache,
                    )
                )
            )
            decoded = decode_document(stored)
        except Exception as exc:  # noqa: BLE001 - retain the rest of the census
            errors.append({"chapter": chapter, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for token, durations in _baseline_durations(decoded).items():
            baseline_durations[token].update(durations)
        indexed = _part_index(decoded)
        segments = [
            (index, row)
            for index, row in enumerate(entry.get("segments") or [])
            if build_mfa_ref(row) is not None
        ]
        segments.sort(key=lambda item: int(item[1].get("time_start", 0)))
        for (left_index, left), (right_index, right) in zip(segments, segments[1:], strict=False):
            record, error = _boundary_record(
                slug,
                chapter,
                left_index,
                left,
                right_index,
                right,
                indexed,
                source_pause_ms=source_pause_ms,
                collapsed_gap_ms=collapsed_gap_ms,
                absorption_ms=absorption_ms,
            )
            if record is not None:
                rows.append(record)
            if error is not None:
                coverage_anomalies.append(
                    {
                        "chapter": chapter,
                        "left_segment_index": left_index,
                        "right_segment_index": right_index,
                        "error": error,
                    }
                )
    return {
        "slug": slug,
        "chapters": chapters,
        "boundaries": rows,
        "baseline_durations": dict(baseline_durations),
        "coverage_anomalies": coverage_anomalies,
        "errors": errors,
    }


def _add_duration_comparisons(
    boundaries: list[dict], baseline_durations: dict[str, Counter[int]]
) -> None:
    baseline_stats = {
        token: _duration_histogram_stats(durations)
        for token, durations in baseline_durations.items()
    }
    for row in boundaries:
        token = str(row["final_sound_token"])
        excluded = _is_variable_long_vowel(token)
        stats = baseline_stats.get(token, _duration_stats([]))
        mean = stats["mean_ms"]
        row["duration_comparison_excluded"] = excluded
        row["duration_comparison_excluded_reason"] = (
            "variable_long_vowel" if excluded else None
        )
        row["baseline_nonfinal_waqf"] = stats
        if excluded or not isinstance(mean, (int, float)) or mean <= 0:
            row["final_sound_duration_ratio_to_baseline_mean"] = None
            row["final_sound_excess_over_baseline_mean_ms"] = None
        else:
            duration = int(row["final_sound_duration_ms"])
            row["final_sound_duration_ratio_to_baseline_mean"] = round(duration / mean, 3)
            row["final_sound_excess_over_baseline_mean_ms"] = round(duration - mean, 3)


def _phoneme_rankings(candidates: list[dict]) -> dict:
    high = [row for row in candidates if row["collapse_likelihood"] == "high"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in high:
        grouped[str(row["final_sound_token"])].append(row)
    ranked, excluded = [], []
    for token, rows in grouped.items():
        durations = [int(row["final_sound_duration_ms"]) for row in rows]
        baseline = rows[0]["baseline_nonfinal_waqf"]
        common = {
            "phoneme": token,
            "candidate_count": len(rows),
            "zeroed_positive_gap_count": sum(
                bool(row["shard_zeroed_positive_acoustic_gap"]) for row in rows
            ),
            "candidate_duration": _duration_stats(durations),
            "baseline_nonfinal_waqf": baseline,
        }
        if rows[0]["duration_comparison_excluded"]:
            excluded.append({**common, "reason": "variable_long_vowel"})
            continue
        mean = baseline["mean_ms"]
        if not isinstance(mean, (int, float)) or mean <= 0:
            excluded.append({**common, "reason": "missing_baseline"})
            continue
        ratios = [duration / mean for duration in durations]
        excesses = [max(0.0, duration - mean) for duration in durations]
        ranked.append(
            {
                **common,
                "mean_ratio_to_baseline": round(statistics.fmean(ratios), 3),
                "median_ratio_to_baseline": round(statistics.median(ratios), 3),
                "mean_positive_excess_ms": round(statistics.fmean(excesses), 3),
                "total_positive_excess_ms": round(sum(excesses), 3),
            }
        )
    ranked.sort(
        key=lambda row: (
            -float(row["total_positive_excess_ms"]),
            -int(row["candidate_count"]),
            str(row["phoneme"]),
        )
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    excluded.sort(key=lambda row: (-int(row["candidate_count"]), str(row["phoneme"])))
    return {
        "schema_version": 1,
        "population": (
            "high-likelihood collapsed-pause candidates; baseline excludes only exact phonemes "
            "that are final in words followed by stop boundaries"
        ),
        "ranking_metric": "total_positive_excess_ms above the non-final-waqf mean",
        "ranked_phonemes": ranked,
        "excluded_phonemes": excluded,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    args = _args()
    manifest = _manifest(args.manifest_url)
    selected = args.slug or sorted(manifest["reciters"])
    work = {}
    for slug in selected:
        record = manifest["reciters"].get(slug)
        if record is None:
            raise SystemExit(f"released reciter absent from manifest: {slug}")
        chapters = sorted(set(args.chapter or record.get("ts_chapters") or []))
        work[slug] = chapters

    if args.cache is not None:
        print(f"prefetching {len(work)} released reciters into {args.cache}", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(_prefetch_reciter, slug, chapters, args.cache): slug
                for slug, chapters in work.items()
            }
            for future in as_completed(futures):
                result = future.result()
                if result["error"]:
                    raise RuntimeError(f"{result['slug']}: {result['error']}")
                print(
                    f"cached {result['slug']}: {result['downloaded']} files",
                    flush=True,
                )

    results = []
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(
                _audit_reciter,
                slug,
                chapters,
                source_pause_ms=args.source_pause_ms,
                collapsed_gap_ms=args.collapsed_gap_ms,
                absorption_ms=args.absorption_ms,
                cache=args.cache,
            ): slug
            for slug, chapters in work.items()
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"{result['slug']}: {len(result['chapters'])} chapters, "
                f"{len(result['boundaries'])} boundaries, {len(result['errors'])} errors",
                flush=True,
            )

    boundaries = sorted(
        (row for result in results for row in result["boundaries"]),
        key=lambda row: (
            row["slug"],
            row["chapter"],
            row["left_segment_index"],
            row["right_segment_index"],
        ),
    )
    baseline_durations: dict[str, Counter[int]] = defaultdict(Counter)
    for result in results:
        for token, durations in result["baseline_durations"].items():
            baseline_durations[token].update(durations)
    _add_duration_comparisons(boundaries, dict(baseline_durations))
    candidates = [
        row
        for row in boundaries
        if row["shard_zeroed_positive_acoustic_gap"] or row["collapse_likelihood"] != "none"
    ]
    phoneme_rankings = _phoneme_rankings(candidates)
    errors = sorted(
        ({"slug": result["slug"], **row} for result in results for row in result["errors"]),
        key=lambda row: (row["slug"], row["chapter"], row.get("left_segment_index", -1)),
    )
    coverage_anomalies = sorted(
        (
            {"slug": result["slug"], **row}
            for result in results
            for row in result["coverage_anomalies"]
        ),
        key=lambda row: (row["slug"], row["chapter"], row["left_segment_index"]),
    )
    by_reciter = []
    for slug in sorted(work):
        rows = [row for row in boundaries if row["slug"] == slug]
        by_reciter.append(
            {
                "slug": slug,
                "chapters": len(work[slug]),
                "boundaries": len(rows),
                "zeroed_positive_gaps": sum(
                    bool(row["shard_zeroed_positive_acoustic_gap"]) for row in rows
                ),
                "high_likelihood_collapses": sum(
                    row["collapse_likelihood"] == "high" for row in rows
                ),
                "medium_likelihood_collapses": sum(
                    row["collapse_likelihood"] == "medium" for row in rows
                ),
                "coverage_anomalies": sum(
                    row["slug"] == slug for row in coverage_anomalies
                ),
            }
        )
    summary = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "bucket": bucket.BUCKETS["prod"],
            "manifest_url": args.manifest_url,
            "manifest_generated_at": manifest.get("generated_at"),
        },
        "thresholds_ms": {
            "source_pause": args.source_pause_ms,
            "collapsed_gap_max": args.collapsed_gap_ms,
            "absorption_min": args.absorption_ms,
        },
        "definitions": {
            "shard_zeroed_positive_acoustic_gap": (
                "v12 decoded boundary is zero although adjacent stored word timings retain a "
                "positive gap"
            ),
            "high_likelihood_collapse": (
                "source segment gap >= source_pause, retained word gap <= collapsed_gap_max, "
                "and the final word/sound extends >= absorption_min past the source segment end"
            ),
            "caveat": (
                "source segment gaps are strong pause evidence, not human-annotated acoustic "
                "onset/offset ground truth"
            ),
            "phoneme_baseline": (
                "same phoneme across released shards, excluding only occurrences that are the "
                "final phoneme of a word whose following boundary is stop"
            ),
            "long_vowel_exclusion": (
                "tokens ending in ':' or 'ː' remain in records but are excluded from comparative "
                "ranking because madd duration is intentionally variable"
            ),
        },
        "totals": {
            "reciters": len(work),
            "chapters": sum(len(one) for one in work.values()),
            "boundaries": len(boundaries),
            "candidate_records": len(candidates),
            "zeroed_positive_gaps": sum(
                bool(row["shard_zeroed_positive_acoustic_gap"]) for row in boundaries
            ),
            "high_likelihood_collapses": sum(
                row["collapse_likelihood"] == "high" for row in boundaries
            ),
            "medium_likelihood_collapses": sum(
                row["collapse_likelihood"] == "medium" for row in boundaries
            ),
            "errors": len(errors),
            "coverage_anomalies": len(coverage_anomalies),
            "ranked_absorption_phonemes": len(phoneme_rankings["ranked_phonemes"]),
            "excluded_absorption_phonemes": len(phoneme_rankings["excluded_phonemes"]),
        },
        "by_reciter": by_reciter,
        "coverage_anomalies": coverage_anomalies,
        "errors": errors,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(args.output / "boundaries.jsonl", boundaries)
    _write_jsonl(args.output / "candidates.jsonl", candidates)
    (args.output / "phoneme_rankings.json").write_text(
        json.dumps(phoneme_rankings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary["totals"], indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
