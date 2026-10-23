"""Put Al-Fatiha's 1:1 back where a reviewer deleted it for being in another voice.

In editions that number the Basmala as 1:1 (Hafs, Shu'ba) the verse is always a
verse: whether it is the reciter's own voice is a flag on its ``specials.json``
entry (``same_voice``), never a reason to drop it. The retired ``basmala_amin``
category had reviewers delete it, which left chapter 1 short a verse in Missing
Verses and in the release.

For each delivery whose chapter 1 has no ``1:1:1`` row, this undoes the latest
un-reverted op that deleted one, through the Inspector's own undo, so the row
comes back exactly as it was and the undo is in edit history. Its
``specials.json`` entry is then re-labelled ``fatiha_verse`` on the restored span,
keeping its voice verdict and confirmation.

Dry run by default. ``--execute`` writes; restart the Space afterwards so its
in-memory ``detailed.json`` is reloaded.

Examples::

    python scripts/backfills/restore_fatiha_basmala.py --slug hatem_fareed_al_waer_mp3quran --bucket dev
    python scripts/backfills/restore_fatiha_basmala.py --slug a b --bucket prod --execute
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("restore_fatiha_basmala")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}
FATIHA = 1
_VERSE_1 = "1:1:1-"
#: Attributed on the undo batch; the pipeline principal marks it as tooling, not a person.
MIGRATION_ACTOR = {"hf_user_id": "pipeline", "login_at_time": "restore_fatiha_basmala", "role": "pipeline"}


@dataclass(frozen=True)
class Deletion:
    batch_id: str
    op_id: str
    saved_at_utc: str
    time_start: int
    time_end: int


def _setup_paths_and_env(bucket: str) -> None:
    """Import the Inspector against ``bucket`` with a private copy of its state DB.

    The DB copy is never uploaded: it syncs whole-file and would overwrite the
    Space's newer state. The edit itself lives in bucket files (detailed.json,
    edit_history.jsonl, specials.json).
    """
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    if bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    os.environ["INSPECTOR_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "inspector.db")

    from services.db import sync

    def refuse(*_args, **_kwargs):
        raise RuntimeError("backfills never upload the state DB")

    sync.upload = refuse
    sync.daily_snapshot = refuse
    sync.pull()


def has_verse_1(entries: list[dict]) -> bool:
    return any(
        (seg.get("matched_ref") or "").startswith(_VERSE_1)
        for e in entries
        if str(e.get("ref", "")).split(":")[0] == str(FATIHA)
        for seg in e.get("segments", [])
    )


def latest_deletion(history: list[dict]) -> Deletion | None:
    """The newest un-reverted op that deleted chapter 1's 1:1 row."""
    reverted_batches = {
        r["reverts_batch_id"] for r in history if r.get("reverts_batch_id") and not r.get("reverts_op_ids")
    }
    reverted_ops = {op for r in history for op in (r.get("reverts_op_ids") or [])}
    found = None
    for batch in history:
        if batch.get("reverts_batch_id") or batch.get("batch_id") in reverted_batches:
            continue
        for op in batch.get("operations", []):
            before = op.get("targets_before") or []
            if op.get("op_id") in reverted_ops or op.get("targets_after") or len(before) != 1:
                continue
            snap = before[0]
            if not (snap.get("matched_ref") or "").startswith(_VERSE_1):
                continue
            found = Deletion(
                batch["batch_id"], op["op_id"], batch.get("saved_at_utc", ""),
                int(snap["time_start"]), int(snap["time_end"]),
            )  # fmt: skip
    return found


def relabel_special(doc: dict, start_ms: int, end_ms: int) -> bool:
    """Re-label chapter 1's Basmala entry as the restored verse; ``False`` if none."""
    for entry in doc.get("chapters", {}).get(str(FATIHA), []):
        if entry["type"] == "Basmala":
            entry.update(source="fatiha_verse", position="pre", start_ms=start_ms, end_ms=end_ms)
            entry["clipped"] = False
            return True
    return False


def _restore(slug: str, execute: bool, deps: dict) -> str:
    entries = deps["load_detailed"](slug) or []
    if not entries:
        return "absent"
    if has_verse_1(entries):
        return "present"
    deletion = latest_deletion(deps["parse_history"](slug) or [])
    if deletion is None:
        log.warning("%s: 1:1 missing but no deletion op found in history", slug)
        return "no_op"
    log.info(
        "%s: undo %s/%s (%s) -> 1:1 [%d-%d]",
        slug, deletion.batch_id, deletion.op_id, deletion.saved_at_utc,
        deletion.time_start, deletion.time_end,
    )  # fmt: skip
    if not execute:
        return "would_restore"
    result = deps["undo_ops"](slug, deletion.batch_id, {deletion.op_id}, actor=deps["actor"])
    if isinstance(result, tuple):
        log.error("%s: undo refused: %s", slug, result)
        return "undo_failed"
    doc = deps["read_specials"](slug)
    if doc is not None and relabel_special(doc, deletion.time_start, deletion.time_end):
        deps["write_specials"](slug, deps["validate_specials"](doc))
    return "restored"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", nargs="+", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument("--execute", action="store_true", help="Write. Without it the script only reports.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _setup_paths_and_env(args.bucket)

    from qua_shared.schemas import Actor
    from qua_shared.schemas.bucket.specials import SpecialsDoc
    from services.activity.history_query import parse_history_for_reciter
    from services.segments.undo import undo_ops
    from services.storage import data_dir
    from services.storage.data_loader import load_detailed

    deps = {
        "load_detailed": load_detailed,
        "parse_history": parse_history_for_reciter,
        "undo_ops": undo_ops,
        "actor": Actor(**MIGRATION_ACTOR),
        "read_specials": data_dir.read_specials_doc,
        "write_specials": data_dir.write_specials_doc,
        "validate_specials": lambda doc: SpecialsDoc.model_validate(doc).model_dump(mode="json"),
    }
    outcomes = {slug: _restore(slug, args.execute, deps) for slug in args.slug}
    for slug, outcome in outcomes.items():
        log.info("%s: %s", slug, outcome)
    log.info("%s: %d deliveries", "EXECUTED" if args.execute else "DRY RUN", len(outcomes))
    return 1 if any(o in ("undo_failed", "no_op") for o in outcomes.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
