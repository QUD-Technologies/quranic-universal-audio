"""Recover join reviews and stage cross-verse wasl merges from a local export.

Dry run is the default. --report-dir writes report.json and summary.txt.
--apply --output stages files in a separate directory; it never uploads.
--live-input optionally names a second local export to check against the snapshot.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qua_shared.schemas.bucket.edit_history import (  # noqa: E402
    EditHistoryBatch,
    parse_edit_history_line,
)
from qua_shared.schemas.bucket.segment import DetailedDocument, JoinVerdict  # noqa: E402
from qua_shared.segment_edit_ops import batch_affects_timestamps  # noqa: E402

CATEGORIES = ("mw_waqf", "mw_wasl_ignore", "mw_wasl_mixed", "cv_waqf", "cv_wasl_merges")
FILES = ("detailed.json", "edit_history.jsonl", "missed_waqf_v1.json")
SNAPSHOT_FIELDS = {"matched_text", "audio_url", "index_at_save", "chapter", "entry_ref"}


def validate_document(document: dict) -> None:
    """Validate persisted fields while preserving snapshot annotations byte-for-byte in input."""
    projected = {
        **document,
        "entries": [
            {
                **e,
                "segments": [
                    {k: v for k, v in s.items() if k not in SNAPSHOT_FIELDS} for s in e["segments"]
                ],
            }
            for e in document["entries"]
        ],
    }
    DetailedDocument.model_validate(projected)


def ref_key(ref: str) -> tuple[int, ...] | None:
    try:
        parts = tuple(map(int, ref.split(":")))
        return parts if len(parts) == 3 and min(parts) > 0 else None
    except (ValueError, AttributeError):
        return None


def bounds(seg: dict) -> tuple | None:
    parts = seg.get("matched_ref", "").split("-")
    start, end = ref_key(parts[0]), ref_key(parts[-1])
    return (start, end) if start and end and start <= end else None


@cache
def word_count(chapter: int, verse: int, riwayah: str) -> int:
    from qua_domain import get_ayah_word_count

    return get_ayah_word_count(chapter, verse, riwayah)


def contiguous(left: dict, right: dict, riwayah: str) -> bool:
    a, b = bounds(left), bounds(right)
    if not a or not b:
        return False
    end, start = a[1], b[0]
    if end[:2] == start[:2]:
        return end[2] + 1 == start[2]
    return (
        end[0] == start[0]
        and end[1] + 1 == start[1]
        and start[2] == 1
        and end[2] == word_count(end[0], end[1], riwayah)
    )


def answer(seg: dict, verdict: Literal["wasl", "waqf"] = "waqf") -> dict:
    return JoinVerdict(
        at_ms=seg["time_end"], after_ref=seg["matched_ref"].split("-")[-1], verdict=verdict
    ).model_dump()


def join_key(j: dict) -> tuple[int, str]:
    return j["at_ms"], j["after_ref"]


def snapshot(seg: dict, entry: dict, index: int) -> dict:
    return {
        **copy.deepcopy(seg),
        "entry_ref": entry["ref"],
        "chapter": int(entry["ref"].split(":")[0]),
        "index_at_save": index,
        **({"audio_url": entry["audio_url"]} if entry.get("audio_url") else {}),
    }


def operation(
    before: list[dict],
    after: list[dict],
    kind: str,
    category: str | None,
    sources: list[str] | None = None,
) -> dict:
    chapter = before[0]["chapter"]
    remaining = {s["segment_uid"] for s in after}
    return {
        "op_id": str(uuid4()),
        "op_type": kind,
        "fix_kind": "audit",
        "op_context_category": category,
        "targets_before": before,
        "targets_after": after,
        "snapshots": {"before": before, "after": after},
        "command": {"backfill": "join_verdicts", "source_op_ids": sources or []},
        **({"merge_direction": "next"} if kind == "merge_segments" else {}),
        "patch": {
            "before": before,
            "after": after,
            "removedIds": [s["segment_uid"] for s in before if s["segment_uid"] not in remaining],
            "insertedIds": [],
            "affectedChapterIds": [chapter],
        },
    }


def merge_skip(
    left: dict,
    right: dict | None,
    entry: dict,
    right_entry: dict | None,
    riwayah: str,
    immediate: bool = True,
) -> str | None:
    if right is None or right_entry is None:
        return "no_next_segment"
    if entry["ref"].split(":")[0] != right_entry["ref"].split(":")[0]:
        return "different_chapter"
    if entry is not right_entry:
        return "different_entry"
    if left.get("audio_url", entry.get("audio_url")) != right.get(
        "audio_url", right_entry.get("audio_url")
    ):
        return "different_audio"
    if not immediate:
        return "not_immediate_in_time"
    if (
        not bounds(left)
        or not bounds(right)
        or left.get("kind") in ("transition", "special", "unmatched")
        or right.get("kind") in ("transition", "special", "unmatched")
    ):
        return "special_or_unmatched"
    if left.get("wrap_word_ranges") or right.get("wrap_word_ranges"):
        return "wrap_word_ranges"
    if left.get("flag") or right.get("flag"):
        return "flagged"
    if left["time_end"] > right["time_start"]:
        return "time_overlap"
    if not contiguous(left, right, riwayah):
        return "noncontiguous_refs"
    seen = {}
    for j in [
        *(left.get("join_verdicts") or []),
        *(right.get("join_verdicts") or []),
        answer(left, "wasl"),
    ]:
        key = join_key(j)
        if key in seen and seen[key] != j["verdict"]:
            return "conflicting_explicit_verdict"
        seen[key] = j["verdict"]
    if not left.get("segment_uid") or not right.get("segment_uid"):
        return "missing_segment_uid"
    return None


def place_verdicts(evidence: list[dict], entries: list[dict]) -> tuple[list[dict], list[dict]]:
    by_entry = defaultdict(list)
    for entry in entries:
        by_entry[entry["ref"]].extend((entry, seg, i) for i, seg in enumerate(entry["segments"]))
    report, ops = [], []
    changes = {}
    for row in evidence:
        j, owners = row["answer"], []
        ref = ref_key(j["after_ref"])
        for entry, seg, i in by_entry[row["entry_ref"]]:
            if not seg["time_start"] < j["at_ms"] <= seg["time_end"]:
                continue
            b = bounds(seg)
            if not b:
                continue
            matches = (
                (j["at_ms"] == seg["time_end"] and ref == b[1])
                if j["verdict"] == "waqf"
                else (j["at_ms"] < seg["time_end"] and b[0] <= ref < b[1])
            )
            if matches:
                owners.append((entry, seg, i))
        if len(owners) != 1:
            report.append(
                {
                    **row,
                    "status": "unplaced",
                    "reason": "ambiguous_current_owner" if owners else "geometry_or_ref_changed",
                }
            )
            continue
        entry, seg, i = owners[0]
        row = {**row, "placed_uid": seg.get("segment_uid")}
        if row["category"] == "cv_waqf" and seg.get("is_wasl"):
            report.append({**row, "status": "superseded", "reason": "current_wasl_merge_candidate"})
            continue
        existing = next(
            (v for v in seg.get("join_verdicts") or [] if join_key(v) == join_key(j)), None
        )
        if existing:
            report.append(
                {
                    **row,
                    "status": "unchanged" if existing == j else "unplaced",
                    "reason": "already_recorded"
                    if existing == j
                    else "conflicting_explicit_verdict",
                }
            )
            continue
        key = id(seg)
        if key not in changes:
            changes[key] = (entry, seg, i, snapshot(seg, entry, i), [])
        changes[key][4].append(row["op_id"])
        verdicts: list[dict] = [*(seg.get("join_verdicts") or []), j]
        seg["join_verdicts"] = sorted(verdicts, key=join_key)
        report.append({**row, "status": "placed"})
    for entry, seg, i, before, sources in changes.values():
        ops.append(
            operation([before], [snapshot(seg, entry, i)], "join_verdict_backfill", None, sources)
        )

    return report, ops


def plan_backfill(
    document: dict, history: list[dict], sidecar: dict | None = None
) -> tuple[dict, list[dict], list[dict]]:
    """Return the candidate, every decision, and reversible operations without I/O."""
    validate_document(document)
    result = copy.deepcopy(document)
    riwayah = document.get("_meta", {}).get("riwayah") or "hafs"
    entries = result["entries"]
    report, ops = [], []
    reverted_ops = {o for b in history for o in b.get("reverts_op_ids", [])}
    reverted_batches = {
        b["reverts_batch_id"]
        for b in history
        if b.get("reverts_batch_id") and not b.get("reverts_op_ids")
    }
    evidence = {}
    inherited_items = dict((sidecar or {}).get("by_uid", {}))
    for batch in history:
        if batch.get("reverts_batch_id") or batch.get("batch_type") == "join_verdict_backfill":
            continue
        for op in batch.get("operations", []):
            kind, category = op.get("op_type"), op.get("op_context_category")
            before, after = op.get("targets_before") or [], op.get("targets_after") or []
            if not before:
                continue
            uid = before[0].get("segment_uid")
            entry_ref = str(
                before[0].get("entry_ref")
                or before[0].get("chapter")
                or before[0]["matched_ref"].split(":")[0]
            )
            source = {"uid": uid, "op_id": op["op_id"], "entry_ref": entry_ref}
            reverted = batch.get("batch_id") in reverted_batches or op["op_id"] in reverted_ops
            item = inherited_items.get(uid)
            if kind == "split_segment" and item and not reverted:
                for child in after:
                    inherited_items.setdefault(child.get("segment_uid"), item)
            candidates = []
            if category == "missed_waqf" and kind in ("split_segment", "ignore_issue"):
                cuts = after[:-1] if kind == "split_segment" else []
                for seg in cuts:
                    if bounds(seg):
                        candidates.append(("mw_waqf", answer(seg)))
                if (
                    not item
                    or not item.get("refs")
                    or len(item["refs"]) != len(item.get("cursors", [])) + 1
                ):
                    report.append(
                        {
                            **source,
                            "category": "mw_wasl_ignore"
                            if kind == "ignore_issue"
                            else "mw_wasl_mixed",
                            "status": "unplaced",
                            "reason": "missing_sidecar_coordinates",
                        }
                    )
                else:
                    cut_times = {s["time_end"] for s in cuts}
                    for i, at in enumerate(item["cursors"]):
                        if not before[0]["time_start"] < at < before[0]["time_end"]:
                            continue
                        if at not in cut_times:
                            candidates.append(
                                (
                                    "mw_wasl_ignore" if kind == "ignore_issue" else "mw_wasl_mixed",
                                    {
                                        "at_ms": at,
                                        "after_ref": item["refs"][i].split("-")[-1],
                                        "verdict": "wasl",
                                    },
                                )
                            )
            else:
                for i, seg in enumerate(after):
                    ref = bounds(seg)
                    if not ref or seg.get("is_wasl"):
                        continue
                    verse_end = ref[1][2] == word_count(*ref[1][:2], riwayah)
                    split_end = (
                        kind == "split_segment"
                        and i < len(after) - 1
                        and verse_end
                        and contiguous(seg, after[i + 1], riwayah)
                        and (next_ref := bounds(after[i + 1])) is not None
                        and next_ref[0][:2] != ref[1][:2]
                    )
                    reviewed = (
                        category == "cross_verse"
                        and kind
                        in ("confirm_reference", "edit_reference", "trim_segment", "set_is_wasl")
                        and verse_end
                    )
                    if split_end or reviewed:
                        candidates.append(("cv_waqf", answer(seg)))
            for cat, verdict in candidates:
                row = {**source, "category": cat, "answer": verdict}
                if reverted:
                    report.append({**row, "status": "unplaced", "reason": "reverted_operation"})
                else:
                    evidence[(entry_ref, *join_key(verdict))] = row

    placed, annotations = place_verdicts(list(evidence.values()), entries)
    report.extend(placed)
    ops.extend(annotations)

    chain_sizes = Counter()
    for entry_index, entry in enumerate(entries):
        segs, i = entry["segments"], 0
        while i < len(segs):
            left = segs[i]
            if not left.get("is_wasl"):
                i += 1
                continue
            right_entry = entry
            right = segs[i + 1] if i + 1 < len(segs) else None
            if right is None:
                right_entry = next((e for e in entries[entry_index + 1 :] if e["segments"]), None)
                right = right_entry["segments"][0] if right_entry else None
            later = sorted(
                (s for s in segs if s is not left and s["time_start"] >= left["time_start"]),
                key=lambda s: (s["time_start"], s["time_end"]),
            )
            immediate = bool(later and later[0] is right) if right_entry is entry else True
            reason = merge_skip(left, right, entry, right_entry, riwayah, immediate)
            row = {
                "category": "cv_wasl_merges",
                "uid": left.get("segment_uid"),
                "right_uid": right.get("segment_uid") if right else None,
                "entry_ref": entry["ref"],
                "at_ms": left["time_end"],
                "after_ref": left["matched_ref"].split("-")[-1],
            }
            if reason:
                report.append({**row, "status": "skipped", "reason": reason})
                i += 1
                continue
            assert right is not None, "merge_skip accepts only pairs with a right segment"
            pre = [snapshot(left, entry, i), snapshot(right, entry, i + 1)]
            merged = copy.deepcopy(left)
            merged.update(
                time_end=right["time_end"],
                matched_ref=f"{left['matched_ref'].split('-')[0]}-{right['matched_ref'].split('-')[-1]}",
                confidence=min(left.get("confidence", 0), right.get("confidence", 0)),
            )
            merged.pop("is_wasl", None)
            if right.get("is_wasl"):
                merged["is_wasl"] = True
            union = {
                join_key(j): j
                for j in [
                    *(left.get("join_verdicts") or []),
                    *(right.get("join_verdicts") or []),
                    answer(left, "wasl"),
                ]
            }
            merged["join_verdicts"] = sorted(union.values(), key=join_key)
            ignored = list(
                dict.fromkeys(
                    [
                        *(left.get("ignored_categories") or []),
                        *(right.get("ignored_categories") or []),
                    ]
                )
            )
            if ignored:
                merged["ignored_categories"] = ignored
            for field in ("word_timings", "pauses", "qalqala_letter", *SNAPSHOT_FIELDS):
                merged.pop(field, None)
            if "qalqala_letter" in right:
                merged["qalqala_letter"] = right["qalqala_letter"]
            segs[i : i + 2] = [merged]
            chain_sizes[merged["segment_uid"]] += 1
            report.append({**row, "status": "merged", "chain_root": merged["segment_uid"]})
            ops.append(
                operation(pre, [snapshot(merged, entry, i)], "merge_segments", "cross_verse")
            )
    for uid, count in chain_sizes.items():
        if count > 1:
            report.append(
                {
                    "category": "merge_chains",
                    "status": "merged",
                    "uid": uid,
                    "merges": count,
                    "pieces": count + 1,
                }
            )
    # Merging can give a previously split WASL join its containing segment.
    deferred = [
        (i, row)
        for i, row in enumerate(report)
        if row.get("reason") == "geometry_or_ref_changed"
        and row.get("answer", {}).get("verdict") == "wasl"
    ]
    retry, annotations = place_verdicts(
        [{k: v for k, v in row.items() if k not in ("status", "reason")} for _, row in deferred],
        entries,
    )
    for (i, _), row in zip(deferred, retry, strict=True):
        report[i] = row
    ops.extend(annotations)
    validate_document(result)
    return result, report, ops


def audit_batches(ops: list[dict], saved_at: str) -> list[dict]:
    by_chapter = defaultdict(list)
    for op in ops:
        by_chapter[op["targets_before"][0]["chapter"]].append(op)
    return [
        EditHistoryBatch.model_validate(
            {
                "batch_id": str(uuid4()),
                "saved_at_utc": saved_at,
                "chapter": ch,
                "batch_type": "join_verdict_backfill",
                "actor": {
                    "hf_user_id": "system",
                    "login_at_time": "join-verdict-backfill",
                    "role": "owner",
                },
                "operations": rows,
            }
        ).model_dump(exclude_none=True, exclude_unset=True)
        for ch, rows in by_chapter.items()
    ]


def counts_for(rows: list[dict]) -> dict:
    counts = Counter({k: 0 for k in (*CATEGORIES, "merge_chains", "unplaced", "skipped")})
    for row in rows:
        if row["status"] in ("placed", "merged"):
            counts[row["category"]] += 1
        elif row["status"] in ("unplaced", "skipped"):
            counts[row["status"]] += 1
    return dict(counts)


def summary_text(report: dict) -> str:
    lines = [f"Join verdict backfill: {report['mode']}", f"Voices: {len(report['reciters'])}"]
    lines += [f"{k}: {v}" for k, v in report["totals"].items()]
    for reason, rows in sorted(report["exceptions"].items()):
        lines.append(f"{reason}: {len(rows)}")
        for row in rows[:3]:
            lines.append("  " + json.dumps(row, ensure_ascii=True))
    lines.append("Voices whose existing timestamps would go stale:")
    lines += ["  " + slug for slug in report["timestamps_would_go_stale"]]
    return "\n".join(lines) + "\n"


def parse_snapshot_history(raw: bytes):
    payload = json.loads(raw)
    payload.pop("save_mode", None)
    return parse_edit_history_line(json.dumps(payload))


def verify_snapshot(source: Path, originals: dict[Path, bytes], live: Path | None = None) -> None:
    for path, raw in originals.items():
        actual = path.read_bytes() if path.exists() else b""
        if actual != raw:
            raise ValueError(f"snapshot changed during planning: {path}")
        if live and path.name == "detailed.json":
            check = live / path.relative_to(source)
            if not check.exists() or check.read_bytes() != raw:
                raise ValueError(f"snapshot mismatch: {path.parent.name}")


def write_verified(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    if path.read_bytes() != raw:
        raise OSError(f"read-back mismatch: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--slug", action="append", help="Limit to these snapshot voices (repeatable)"
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live-input", type=Path)
    args = parser.parse_args(argv)
    source = args.input.resolve(strict=True)
    if bool(args.output) != args.apply:
        parser.error("--apply and --output must be used together")
    for target in (args.output, args.report_dir, args.report):
        if target and (
            target.exists()
            or target.resolve() == source
            or source in target.resolve().parents
            or target.resolve() in source.parents
        ):
            parser.error("outputs must be new paths outside the input tree")
    paths = sorted(source.glob("*/detailed.json"))
    if args.slug:
        missing = set(args.slug) - {p.parent.name for p in paths}
        if missing:
            parser.error(f"missing snapshot voices: {sorted(missing)}")
        paths = [p for p in paths if p.parent.name in args.slug]
    if not paths:
        parser.error("no <slug>/detailed.json files in --input")
    report = {
        "mode": "staged-apply" if args.apply else "dry-run",
        "reciters": {},
        "totals": {},
        "exceptions": {},
        "timestamps_would_go_stale": [],
    }
    totals, exceptions, originals, staged = Counter(), defaultdict(list), {}, []
    saved_at = datetime.now(UTC).isoformat()
    for path in paths:
        raw = {
            name: path.with_name(name).read_bytes() if path.with_name(name).exists() else b""
            for name in FILES
        }
        originals.update({path.with_name(name): value for name, value in raw.items()})
        before = json.loads(raw["detailed.json"])
        history = [
            b.model_dump(exclude_unset=True)
            for line in raw["edit_history.jsonl"].splitlines()
            if (b := parse_snapshot_history(line)) is not None
        ]
        sidecar = json.loads(raw["missed_waqf_v1.json"]) if raw["missed_waqf_v1.json"] else {}
        after, rows, ops = plan_backfill(before, history, sidecar)
        batches = audit_batches(ops, saved_at)
        counts = counts_for(rows)
        prior = {s.get("segment_uid"): s for e in before["entries"] for s in e["segments"]}
        current = {s.get("segment_uid"): s for e in after["entries"] for s in e["segments"]}
        counts["segments_changed"] = sum(
            prior.get(uid) != current.get(uid) for uid in prior.keys() | current.keys()
        )
        totals.update(counts)
        slug = path.parent.name
        stale = any(batch_affects_timestamps(b) for b in batches)
        if stale:
            report["timestamps_would_go_stale"].append(slug)
        for row in rows:
            if row["status"] in ("skipped", "unplaced"):
                exceptions[row["reason"]].append({"slug": slug, **row})
        report["reciters"][slug] = {
            "counts": counts,
            "timestamps_would_go_stale": stale,
            "decisions": rows,
            "changes": ops,
            "source_sha256": {
                name: hashlib.sha256(value).hexdigest() for name, value in raw.items()
            },
        }
        if args.apply:
            prefix = raw["edit_history.jsonl"]
            if batches and prefix and not prefix.endswith(b"\n"):
                prefix += b"\n"
            staged.append(
                (
                    slug,
                    {
                        "detailed.json": (
                            json.dumps(after, ensure_ascii=False, indent=2) + "\n"
                        ).encode()
                        if ops
                        else raw["detailed.json"],
                        "edit_history.jsonl": prefix
                        + b"".join(
                            (json.dumps(b, ensure_ascii=False) + "\n").encode() for b in batches
                        ),
                        "missed_waqf_v1.json": raw["missed_waqf_v1.json"],
                    },
                )
            )
    report["totals"], report["exceptions"] = dict(totals), dict(exceptions)
    verify_snapshot(source, originals, args.live_input.resolve() if args.live_input else None)
    text = summary_text(report)
    report_bytes = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    for target in (args.output, args.report_dir):
        if not target:
            continue
        target.mkdir(parents=True, exist_ok=False)
        write_verified(target / "report.json", report_bytes)
        write_verified(target / "summary.txt", text.encode())
    if args.output:
        for slug, files in staged:
            dest = args.output / slug
            dest.mkdir()
            for name, value in files.items():
                if value:
                    write_verified(dest / name, value)
    if args.report:
        with args.report.open("xb") as out:
            out.write(report_bytes)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
