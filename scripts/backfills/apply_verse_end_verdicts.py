"""Split unsettled cross-verse segments at the verse ends the lab read as waqf.

The boundary-head lab's hidden_pause sidecar step judges every verse end inside a
segment whose reference crosses a verse: where the phoneme lattice and the silence
at the recording's noise floor agree, the verdict is written to
``verse_ends_applied.json`` (``by_uid`` → ``joins[]`` with ``after_ref`` /
``next_ref`` in the delivery's own numbering, ``verdict`` ``waqf`` / ``wasl`` and
``cursor_ms``). This script applies the ``waqf`` verdicts: each such segment is split
at those cursors, every new boundary WAQF (``is_wasl`` false), through the same
full-replace save a reviewer's split takes, logged as ``split_segment`` with
``op_context_category`` ``cross_verse`` and ``fix_kind`` ``auto_fix`` and a real
patch, so the History panel and undo treat it like any other split. ``wasl``
verdicts need no write: the segment already stays whole.

A segment is skipped when it is no longer live with the bounds the lab heard, or a
cursor does not fall strictly inside it. Each chapter save waits until both files read
back before the next save reads them, and an applied split that reached detailed.json
without its history op is logged. Dry run by default; restart the Space after
``--apply`` so its in-memory detailed.json is reloaded.

Examples::

    python3 scripts/backfills/apply_verse_end_verdicts.py --slug hatem_fareed_al_waer_mp3quran \\
        --applied runs/hatem/verse_ends_applied.json --bucket prod
    python3 scripts/backfills/apply_verse_end_verdicts.py --slug ... --applied ... --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("apply_verse_end_verdicts")

CATEGORY = "cross_verse"
FIX_KIND = "auto_fix"
WAQF = "waqf"
_BUCKETS = {
    "dev": "hetchyy/quranic-inspector-bucket-dev",
    "prod": "hetchyy/quranic-inspector-bucket",
}
#: Local runs write through the bucket API and read back through a cache that can lag,
#: so each save waits until it reads back before the next one reads the files.
VISIBLE_TIMEOUT_S = 300
VISIBLE_POLL_S = 3
_OWNER_ID = "684abe5b6327ae8863d106d2"
_OWNER_LOGIN = "hetchyy"


def _setup_paths_and_env(bucket: str) -> None:
    """Import the Inspector against ``bucket`` with a read-only copy of its state DB.

    Saves read the delivery catalog (its riwayah) from the DB. The copy is pulled
    to a private temp file and uploads are refused: the DB syncs whole-file, so a
    local upload would overwrite the Space's newer state.
    """
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    os.environ["INSPECTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "inspector.db")

    from services.db import sync

    def refuse(*_args, **_kwargs):
        raise RuntimeError("backfills never upload the state DB")

    sync.upload = refuse
    sync.daily_snapshot = refuse
    sync.pull()


@dataclass(frozen=True)
class Split:
    chapter: int
    uid: str
    cursors: tuple[int, ...]
    refs: tuple[str, ...]  # one per piece


def plan_splits(
    entries: list[dict], applied: dict[str, dict], chapter_of: Callable[[str], int]
) -> tuple[list[Split], dict[str, int]]:
    """The splits the ``waqf`` verdicts of ``applied`` ask for, and why others were skipped."""
    live = {
        seg["segment_uid"]: (seg, chapter_of(entry["ref"]))
        for entry in entries
        for seg in entry.get("segments", [])
        if seg.get("segment_uid")
    }
    skipped: dict[str, int] = defaultdict(int)
    out: list[Split] = []
    for uid, item in applied.items():
        joins = sorted(
            (j for j in item.get("joins", []) if j.get("verdict") == WAQF),
            key=lambda j: j["cursor_ms"],
        )
        if not joins:
            continue
        found = live.get(uid)
        if found is None:
            skipped["gone"] += 1
            continue
        seg, chapter = found
        if (seg["time_start"], seg["time_end"]) != (item["start_ms"], item["end_ms"]):
            skipped["moved"] += 1
            continue
        cursors = tuple(j["cursor_ms"] for j in joins)
        inside = all(seg["time_start"] < c < seg["time_end"] for c in cursors)
        if not inside or len(set(cursors)) != len(cursors):
            skipped["cursor_outside"] += 1
            continue
        first, _, last = (seg.get("matched_ref") or "").partition("-")
        if not last:
            skipped["no_range"] += 1
            continue
        edges = [first, *(j["next_ref"] for j in joins)]
        ends = [*(j["after_ref"] for j in joins), last]
        out.append(
            Split(
                chapter, uid, cursors, tuple(f"{a}-{b}" for a, b in zip(edges, ends, strict=True))
            )
        )
    return out, dict(skipped)


def _snapshot(seg: dict, index: int, chapter: int, entry: dict) -> dict:
    """The frontend's ``snapshotSeg`` for a detailed.json segment."""
    snap = {
        "segment_uid": seg.get("segment_uid"),
        "index_at_save": index,
        "audio_url": entry.get("audio") or None,
        "time_start": seg["time_start"],
        "time_end": seg["time_end"],
        "matched_ref": seg.get("matched_ref") or "",
        "confidence": seg.get("confidence") or 0,
        "entry_ref": entry.get("ref"),
        "chapter": chapter,
    }
    if seg.get("wrap_word_ranges"):
        snap["wrap_word_ranges"] = seg["wrap_word_ranges"]
    if seg.get("ignored_categories"):
        snap["ignored_categories"] = list(seg["ignored_categories"])
    if seg.get("is_wasl"):
        snap["is_wasl"] = True
    if seg.get("join_verdicts"):
        snap["join_verdicts"] = copy.deepcopy(seg["join_verdicts"])
    return snap


def _pieces(seg: dict, split: Split, new_uids: list[str]) -> list[dict]:
    """``_reduceSplit``: piece 0 keeps the uid, new boundaries are WAQF, the last
    piece keeps the parent's ``is_wasl``, repetition metadata is dropped."""
    bounds = [seg["time_start"], *split.cursors, seg["time_end"]]
    out = []
    for i, ref in enumerate(split.refs):
        piece = copy.deepcopy(seg)
        piece.pop("wrap_word_ranges", None)
        piece.pop("word_timings", None)
        piece["time_start"], piece["time_end"] = bounds[i], bounds[i + 1]
        piece["matched_ref"] = ref
        if i:
            piece["segment_uid"] = new_uids[i - 1]
        piece["is_wasl"] = bool(seg.get("is_wasl")) if i == len(split.refs) - 1 else False
        out.append(piece)
    return out


def chapter_save(
    entries: list[dict],
    chapter: int,
    splits: list[Split],
    uuid: Callable[[], str],
    chapter_of: Callable[[str], int],
) -> dict:
    """The full-replace save payload for ``chapter`` with ``splits`` applied."""
    by_uid = {s.uid: s for s in splits if s.chapter == chapter}
    segments: list[dict] = []
    operations: list[dict] = []
    index = 0
    for entry in entries:
        if chapter_of(entry["ref"]) != chapter:
            continue
        for seg in entry.get("segments", []):
            split = by_uid.get(seg.get("segment_uid"))
            if split is None:
                segments.append({**seg, "audio_url": entry.get("audio") or ""})
                index += 1
                continue
            new_uids = [uuid() for _ in split.cursors]
            pieces = _pieces(seg, split, new_uids)
            before = [_snapshot(seg, index, chapter, entry)]
            after = [_snapshot(p, index + i, chapter, entry) for i, p in enumerate(pieces)]
            operations.append(
                {
                    "op_id": uuid(),
                    "op_type": "split_segment",
                    "op_context_category": CATEGORY,
                    "fix_kind": FIX_KIND,
                    "targets_before": before,
                    "targets_after": after,
                    "affected_chapters": [chapter],
                    "patch": {
                        "before": before,
                        "after": after,
                        "removedIds": [],
                        "insertedIds": new_uids,
                        "affectedChapterIds": [chapter],
                    },
                }
            )
            segments.extend({**p, "audio_url": entry.get("audio") or ""} for p in pieces)
            index += len(pieces)
    return {"full_replace": True, "segments": segments, "operations": operations}


def unlogged_splits(
    entries: list[dict],
    applied: dict[str, dict],
    history: list[dict],
    chapter_of: Callable[[str], int],
    uuid: Callable[[], str],
) -> dict[int, list[dict]]:
    """``split_segment`` ops, by chapter, for applied splits that reached detailed.json
    but not edit history (a write the history read-modify-write lost)."""
    logged = {
        op["targets_before"][0].get("segment_uid")
        for batch in history
        for op in batch.get("operations") or []
        if op.get("op_type") == "split_segment" and op.get("targets_before")
    }
    by_chapter: dict[int, list[tuple[dict, dict, int]]] = defaultdict(list)
    for entry in entries:
        chapter = chapter_of(entry["ref"])
        for seg in entry.get("segments", []):
            by_chapter[chapter].append((seg, entry, len(by_chapter[chapter])))
    out: dict[int, list[dict]] = defaultdict(list)
    for uid, item in applied.items():
        if uid in logged or not any(j.get("verdict") == WAQF for j in item.get("joins", [])):
            continue
        pieces = sorted(
            (
                row
                for row in by_chapter.get(item["chapter"], [])
                if item["start_ms"] <= row[0]["time_start"] and row[0]["time_end"] <= item["end_ms"]
            ),
            key=lambda row: row[0]["time_start"],
        )
        if len(pieces) < 2 or pieces[0][0].get("segment_uid") != uid:
            continue
        first, entry, index = pieces[0]
        last = pieces[-1][0]
        parent = {
            **first,
            "time_end": item["end_ms"],
            "matched_ref": first["matched_ref"].partition("-")[0]
            + "-"
            + last["matched_ref"].partition("-")[2],
            "is_wasl": bool(last.get("is_wasl")),
        }
        chapter = item["chapter"]
        before = [_snapshot(parent, index, chapter, entry)]
        after = [
            _snapshot(seg, index + k, chapter, entry) for k, (seg, _e, _i) in enumerate(pieces)
        ]
        out[chapter].append(
            {
                "op_id": uuid(),
                "op_type": "split_segment",
                "op_context_category": CATEGORY,
                "fix_kind": FIX_KIND,
                "targets_before": before,
                "targets_after": after,
                "affected_chapters": [chapter],
                "patch": {
                    "before": before,
                    "after": after,
                    "removedIds": [],
                    "insertedIds": [t["segment_uid"] for t in after[1:]],
                    "affectedChapterIds": [chapter],
                },
            }
        )
    return dict(out)


def _await_visible(read: Callable[[], bytes | None], markers: list[str], what: str) -> None:
    """Block until every marker reads back from ``read``."""
    from huggingface_hub import hffs  # type: ignore[import-not-found]

    deadline = time.monotonic() + VISIBLE_TIMEOUT_S
    while True:
        hffs.invalidate_cache()
        raw = read() or b""
        if all(m.encode() in raw for m in markers):
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what}: write not visible after {VISIBLE_TIMEOUT_S}s")
        time.sleep(VISIBLE_POLL_S)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--applied", required=True, help="the lab's verse_ends_applied.json")
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument(
        "--apply", action="store_true", help="Mutate. Without it the script only reports."
    )
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _setup_paths_and_env(args.bucket)

    from constants import HISTORY_SCHEMA_VERSION
    from qua_shared.schemas import Actor
    from qua_shared.schemas.config.access import Role
    from services.activity.history_query import parse_history_for_reciter
    from services.segments.save import save_seg_data
    from services.state import state as state_service
    from services.storage import cache, data_dir
    from services.storage.data_loader import load_detailed
    from utils.references import chapter_from_ref
    from utils.uuid7 import uuid7

    state_service.hydrate()
    # The catalog lives in the Space's database; a local run takes the edition
    # from detailed.json, which sdk_riwayah_for cross-checks against anyway.
    from services.reference import delivery_edition
    from services.storage import data_loader

    delivery_edition.inspector_riwayah_for = lambda slug: (
        (data_loader.seg_meta(slug) or {}).get("riwayah") or delivery_edition.DEFAULT_RIWAYAH
    )
    applied = json.loads(Path(args.applied).read_text(encoding="utf-8")).get("by_uid", {})
    entries = load_detailed(args.slug)
    if not entries:
        log.error("no detailed entries for slug=%s", args.slug)
        return 2
    splits, skipped = plan_splits(entries, applied, chapter_from_ref)
    history = parse_history_for_reciter(args.slug) or []
    unlogged = unlogged_splits(entries, applied, history, chapter_from_ref, uuid7)
    cuts = sum(len(s.cursors) for s in splits)
    log.info(
        "%s: %d segments, %d waqf cuts; skipped %s; %d applied splits missing from history",
        args.slug,
        len(splits),
        cuts,
        skipped,
        sum(len(v) for v in unlogged.values()),
    )
    if not (splits or unlogged) or not args.apply:
        if splits or unlogged:
            log.info("DRY RUN — re-run with --apply to mutate.")
        return 0

    actor = Actor(hf_user_id=_OWNER_ID, login_at_time=_OWNER_LOGIN, role=Role.OWNER)

    def read_history() -> bytes | None:
        try:
            return data_dir.get_backend().read_bytes(data_dir.edit_history_path(args.slug))
        except Exception:  # noqa: BLE001 — absent or mid-write; the poll retries
            return None

    def fresh_entries() -> list[dict]:
        cache.pop_seg_caches_affected_by_segment_edit(args.slug)
        return load_detailed(args.slug)

    failed = 0
    for chapter in sorted({s.chapter for s in splits}):
        payload = chapter_save(fresh_entries(), chapter, splits, uuid7, chapter_from_ref)
        result = save_seg_data(args.slug, chapter, payload, actor=actor)
        if isinstance(result, tuple):
            log.error("chapter %d: save failed %s", chapter, result)
            failed += 1
            continue
        ops = payload["operations"]
        new_uids = [u for op in ops for u in op["patch"]["insertedIds"]]
        _await_visible(
            lambda: data_dir.read_detailed_bytes(args.slug), new_uids, f"ch {chapter} detailed"
        )
        _await_visible(read_history, [op["op_id"] for op in ops], f"ch {chapter} history")
        log.info("chapter %d: %d splits saved", chapter, len(ops))
    for chapter, ops in sorted(unlogged.items()):
        batch = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "batch_id": uuid7(),
            "chapter": chapter,
            "saved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "operations": ops,
            "actor": actor.model_dump(mode="json"),
        }
        data_dir.append_edit_history(args.slug, batch)
        _await_visible(read_history, [batch["batch_id"]], f"ch {chapter} history repair")
        log.info("chapter %d: %d applied splits logged", chapter, len(ops))
    cache.pop_seg_caches_affected_by_segment_edit(args.slug)
    cache.pop_seg_split_group_index(args.slug)
    log.info("DONE: %d chapters saved, %d failed", len({s.chapter for s in splits}), failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
