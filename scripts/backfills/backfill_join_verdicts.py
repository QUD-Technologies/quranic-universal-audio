"""Recover explicit join answers from a LOCAL review-data export. Dry run by default.

    python scripts/backfills/backfill_join_verdicts.py --input export/reciters
    python scripts/backfills/backfill_join_verdicts.py --input export/reciters --report plan.json
    python scripts/backfills/backfill_join_verdicts.py --input export/reciters --apply --output staged

Input contains <slug>/detailed.json and optional edit_history.jsonl,
and wasl_recheck_v1.json. --apply creates a NEW local
output directory with changed detailed/history files and a provenance report.
It never modifies the input, imports Inspector services, or accesses a bucket.
Ambiguous ignores, unlabelled splits, stale geometry and rechecks stay unset.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qua_shared.schemas.bucket.edit_history import (  # noqa: E402
    EditHistoryBatch,
    parse_edit_history_line,
)
from qua_shared.schemas.bucket.segment import DetailedDocument, JoinVerdict  # noqa: E402


def _geometry(seg: dict) -> tuple:
    return seg.get("time_start"), seg.get("time_end"), seg.get("matched_ref")


def _edge(seg: dict, value: bool) -> dict:
    return JoinVerdict(
        at_ms=seg["time_end"],
        after_ref=seg["matched_ref"].split("-")[-1],
        verdict="wasl" if value else "waqf",
    ).model_dump()


def _utc(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=UTC)
    except ValueError:
        return None


def plan_backfill(
    document: dict, history: list[dict], recheck: dict | None = None
) -> tuple[dict, list[dict]]:
    """Pure, conservative plan. Latest geometry changes invalidate earlier evidence.

    A legacy set_is_wasl op is an explicit answer (its absent flag means false).
    Split true flags are explicit WASL. For Low Confidence Waqf ONLY, a supplied
    command.wasls false is a deliberate retained stop. Other false split flags
    may be the old pending default and cannot establish a WAQF answer.
    """
    DetailedDocument.model_validate(document)
    result = copy.deepcopy(document)
    live: dict[str, dict] = {}
    for entry in result.get("entries", []):
        for seg in entry.get("segments", []):
            uid = seg.get("segment_uid")
            if uid:
                if uid in live:
                    raise ValueError(f"duplicate segment_uid: {uid}")
                live[uid] = seg
    reverted_ops = {oid for b in history for oid in b.get("reverts_op_ids", [])}
    reverted_batches = {
        b["reverts_batch_id"]
        for b in history
        if b.get("reverts_batch_id") and not b.get("reverts_op_ids")
    }
    evidence: dict[str, tuple[dict, dict, str, str | None, str]] = {}
    report: list[dict] = []
    for batch in history:
        if batch.get("batch_id") in reverted_batches or batch.get("reverts_batch_id"):
            continue
        for op in batch.get("operations", []):
            if op.get("op_id") in reverted_ops:
                continue
            kind = op.get("op_type") or op.get("kind")
            before = op.get("targets_before") or []
            after = op.get("targets_after") or []
            after_by_uid = {s.get("segment_uid"): s for s in after}
            # Do not transplant an old edge answer onto a new join just because
            # a split/merge/trim kept the same UID.
            for snap in before:
                uid = snap.get("segment_uid")
                if uid in evidence and _geometry(after_by_uid.get(uid, {})) != _geometry(
                    evidence[uid][0]
                ):
                    del evidence[uid]
            command = op.get("command") or {}
            for i, snap in enumerate(after):
                uid = snap.get("segment_uid")
                if not uid:
                    continue
                value = None
                reason = None
                if kind == "set_is_wasl" and not command.get("join"):
                    value = command.get("is_wasl", snap.get("is_wasl", False))
                    reason = "explicit_set_is_wasl"
                elif kind == "split_segment" and i < len(after) - 1:
                    if snap.get("is_wasl") is True:
                        value, reason = True, "explicit_split_wasl"
                    else:
                        picks = command.get("wasls") or []
                        if (
                            op.get("op_context_category") == "missed_waqf"
                            and i < len(picks)
                            and picks[i] is False
                        ):
                            value, reason = False, "reviewed_waqf_cut"
                        else:
                            report.append(
                                {
                                    "uid": uid,
                                    "status": "ambiguous",
                                    "reason": "split_without_explicit_answer",
                                    "op_id": op.get("op_id"),
                                }
                            )
                elif kind == "ignore_issue" and op.get("op_context_category") == "missed_waqf":
                    report.append(
                        {
                            "uid": uid,
                            "status": "ambiguous",
                            "reason": "ignore_does_not_identify_join_answers",
                            "op_id": op.get("op_id"),
                        }
                    )
                if value is not None:
                    if type(value) is not bool:
                        raise ValueError(f"non-boolean answer in op {op.get('op_id')}")
                    evidence[uid] = (
                        snap,
                        _edge(snap, value),
                        op["op_id"],
                        batch.get("saved_at_utc"),
                        reason,
                    )

    recheck = recheck or {}
    since = _utc(recheck.get("_meta", {}).get("created_at"))
    for uid, (snapshot, answer, op_id, saved, reason) in evidence.items():
        seg = live.get(uid)
        row = {"uid": uid, "answer": answer, "op_id": op_id, "reason": reason}
        if not seg:
            report.append({**row, "status": "missing_segment"})
            continue
        if _geometry(seg) != _geometry(snapshot):
            report.append({**row, "status": "stale_geometry"})
            continue
        if uid in recheck.get("by_uid", {}):
            saved_at = _utc(saved)
            if since is None or saved_at is None or saved_at <= since:
                report.append({**row, "status": "recheck_pending"})
                continue
        key = (answer["at_ms"], answer["after_ref"])
        existing = next(
            (j for j in seg.get("join_verdicts") or [] if (j["at_ms"], j["after_ref"]) == key), None
        )
        if existing:
            report.append({**row, "status": "unchanged" if existing == answer else "conflict"})
            continue
        seg["join_verdicts"] = sorted(
            [*(seg.get("join_verdicts") or []), answer], key=lambda j: (j["at_ms"], j["after_ref"])
        )
        # Keep the downstream edge flag consistent with the recovered answer.
        seg["is_wasl"] = answer["verdict"] == "wasl"
        report.append({**row, "status": "recoverable"})

    # Round-trip the candidate through the writer contract, without adding defaults.
    result = DetailedDocument.model_validate(result).model_dump(by_alias=True, exclude_unset=True)
    return result, report


def _audit(before: dict, after: dict, report: list[dict], saved_at: str) -> list[dict]:
    old = {
        s["segment_uid"]: (e["ref"], s)
        for e in before["entries"]
        for s in e["segments"]
        if s.get("segment_uid")
    }
    new = {
        s["segment_uid"]: s for e in after["entries"] for s in e["segments"] if s.get("segment_uid")
    }
    batches = []
    for row in report:
        if row["status"] != "recoverable":
            continue
        uid = row["uid"]
        entry_ref, prev = old[uid]
        chapter = int(entry_ref.split(":")[0])
        pre, post = (
            [{**prev, "entry_ref": entry_ref, "chapter": chapter}],
            [{**new[uid], "entry_ref": entry_ref, "chapter": chapter}],
        )
        batch = EditHistoryBatch.model_validate(
            {
                "batch_id": str(uuid4()),
                "saved_at_utc": saved_at,
                "chapter": chapter,
                "batch_type": "join_verdict_backfill",
                "actor": {
                    "hf_user_id": "system",
                    "login_at_time": "join-verdict-backfill",
                    "role": "owner",
                },
                "operations": [
                    {
                        "op_id": str(uuid4()),
                        "op_type": "set_is_wasl",
                        "fix_kind": "audit",
                        "command": {
                            "type": "setIsWasl",
                            "segmentUid": uid,
                            "is_wasl": row["answer"]["verdict"] == "wasl",
                            "join": row["answer"],
                            "source_op_id": row["op_id"],
                        },
                        "targets_before": pre,
                        "targets_after": post,
                        "patch": {
                            "before": pre,
                            "after": post,
                            "removedIds": [],
                            "insertedIds": [],
                            "affectedChapterIds": [chapter],
                        },
                    }
                ],
            }
        )
        batches.append(batch.model_dump(exclude_none=True, exclude_unset=True))
    return batches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="Local directory containing reciter folders"
    )
    parser.add_argument("--report", type=Path, help="Optional local JSON report")
    parser.add_argument(
        "--apply", action="store_true", help="Stage changed files in a NEW local output directory"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    source = args.input.resolve(strict=True)
    if bool(args.output) != args.apply:
        parser.error("--apply and --output must be used together")
    target = args.output.resolve() if args.output else None
    if target and (
        target.exists() or source == target or source in target.parents or target in source.parents
    ):
        parser.error("--output must be a new directory outside the input tree")
    if args.report and (args.report.resolve() == source or source in args.report.resolve().parents):
        parser.error("--report must be outside the input tree")
    paths = sorted(source.glob("*/detailed.json"))
    if not paths:
        parser.error("no <slug>/detailed.json files in --input")
    plans = []
    summary = {"mode": "apply" if args.apply else "dry-run", "reciters": {}}
    for path in paths:
        raw = path.read_bytes()
        history_path = path.with_name("edit_history.jsonl")
        history_raw = history_path.read_bytes() if history_path.exists() else b""
        history = [
            batch.model_dump(exclude_unset=True)
            for line in history_raw.splitlines()
            if (batch := parse_edit_history_line(line)) is not None
        ]
        recheck_path = path.with_name("wasl_recheck_v1.json")
        recheck_raw = recheck_path.read_bytes() if recheck_path.exists() else b""
        before = json.loads(raw)
        after, rows = plan_backfill(before, history, json.loads(recheck_raw) if recheck_raw else {})
        summary["reciters"][path.parent.name] = {
            "counts": dict(Counter(row["status"] for row in rows)),
            "joins": rows,
            "input_sha256": {
                "detailed.json": hashlib.sha256(raw).hexdigest(),
                "edit_history.jsonl": hashlib.sha256(history_raw).hexdigest(),
                "wasl_recheck_v1.json": hashlib.sha256(recheck_raw).hexdigest(),
            },
        }
        plans.append((path.parent.name, before, after, rows, history_raw))
    # Validate every reciter and every audit batch BEFORE creating any staged files.
    saved_at = datetime.now(UTC).isoformat()
    staged = [
        (slug, after, history_raw, _audit(before, after, rows, saved_at))
        for slug, before, after, rows, history_raw in plans
    ]
    if target:
        target.mkdir(parents=True, exist_ok=False)
        for slug, after, history_raw, batches in staged:
            if not batches:
                continue
            dest = target / slug
            dest.mkdir()
            (dest / "detailed.json").write_text(
                json.dumps(after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            prefix = history_raw + (
                b"\n" if history_raw and not history_raw.endswith(b"\n") else b""
            )
            (dest / "edit_history.jsonl").write_bytes(
                prefix
                + b"".join(
                    json.dumps(b, ensure_ascii=False).encode("utf-8") + b"\n" for b in batches
                )
            )
        (target / "report.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.report:
        with args.report.open("x", encoding="utf-8") as report_file:
            json.dump(summary, report_file, indent=2)
            report_file.write("\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
