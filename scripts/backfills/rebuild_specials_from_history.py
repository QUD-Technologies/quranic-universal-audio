"""Rebuild a reciter's ``specials.json`` from the pipeline's strip events in its edit history.

A delivery aligned before the align run wrote ``specials.json`` has none. The run's
strips are still in ``edit_history.jsonl`` (the ``pipeline`` actor's whole-segment
``delete_segment`` ops), so the same builder an align run uses
(``services.segments.specials_doc.specials_from_run``) can rebuild it: the openers
before ayah 1, specials after the last ayah and, in Hafs / Shu'ba, Al-Fatiha's 1:1.
The 1:1 gets no voice verdict here (the aligner scores it at align time); a
maintainer can call it from the Segments notice.

Dry run by default; refuses to overwrite an existing ``specials.json`` without ``--force``.

Examples::

    python scripts/backfills/rebuild_specials_from_history.py --slug raad_al_kurdi_mp3quran
    python scripts/backfills/rebuild_specials_from_history.py --slug raad_al_kurdi_mp3quran --execute
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter

log = logging.getLogger("rebuild_specials_from_history")


def strip_events(history: list[dict]) -> list:
    """The pipeline's whole-segment deletions, as align-run audit events."""
    from qua_shared.schemas.bucket.staged_run import PipelineAuditEvent, PipelineSegSnapshot

    events = []
    for batch in history:
        if (batch.get("actor") or {}).get("role") != "pipeline":
            continue
        for op in batch.get("operations", []):
            if op.get("op_type") != "delete_segment" or op.get("targets_after"):
                continue
            for target in op.get("targets_before") or []:
                snap = PipelineSegSnapshot(
                    index_at_save=target.get("index_at_save", 0),
                    time_start=target["time_start"],
                    time_end=target["time_end"],
                    matched_ref=target.get("matched_ref") or "",
                    confidence=target.get("confidence"),
                )
                chapter = int(target.get("chapter") or batch["chapter"])
                events.append(
                    PipelineAuditEvent(
                        kind="delete_segment", chapter=chapter, targets_before=[snap]
                    )
                )
    return events


def rebuild(slug: str):
    from services.reference.delivery_edition import sdk_riwayah_for
    from services.segments.specials_doc import specials_from_run
    from services.storage import data_dir
    from services.storage.data_loader import load_detailed
    from services.storage.hf_bucket import get_backend

    raw = get_backend().read_bytes(data_dir.edit_history_path(slug)).decode()
    history = [json.loads(line) for line in raw.splitlines() if line.strip()]
    by_chapter: dict[int, dict] = {}
    for entry in load_detailed(slug):
        chapter = int(str(entry["ref"]).split(":")[0])
        by_chapter.setdefault(chapter, {"segments": []})["segments"].extend(entry["segments"])
    return specials_from_run(
        strip_events(history),
        by_chapter,
        generated_at=history[0]["saved_at_utc"],
        riwayah=sdk_riwayah_for(slug),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--bucket", choices=("dev", "prod"), default="prod")
    ap.add_argument("--force", action="store_true", help="overwrite an existing specials.json")
    ap.add_argument("--execute", action="store_true", help="Write. Without it only count.")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    import splice_opener_readings as S

    S._setup_paths_and_env(args.bucket)
    from services.storage import data_dir

    if data_dir.read_specials_doc(args.slug) is not None and not args.force:
        log.error("%s already has specials.json (pass --force to rebuild it)", args.slug)
        return 1
    doc = rebuild(args.slug)
    if doc is None:
        log.error("%s: the edit history has no specials to rebuild", args.slug)
        return 1
    kinds = Counter(
        (e.type, e.position, e.source) for entries in doc.chapters.values() for e in entries
    )
    log.info("%s: %d chapters %s", args.slug, len(doc.chapters), dict(kinds))
    if args.execute:
        data_dir.write_specials_doc(args.slug, doc.model_dump(mode="json"))
        log.info("EXECUTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
