"""End every recorded opener where the next sound starts: the next opener or ayah 1.

The opener backfill took a lead-in opener's span from the aligner's own scan of the
lead-in, which pads its edges independently of the chapter's segments, so an opener
could end up to ~0.3 s inside the next opener or ayah 1's first segment. Openers live
in ``specials.json``, not ``detailed.json``, so the segments' no-overlap rule never
covered them; the word times then aligned over the borrowed audio.

This ends each ``pre`` opener (Al-Fatiha's own 1:1 aside) at the start of whatever
follows it, marks it ``clipped`` and drops its stale word times. ``--redo`` writes
``{slug: [chapters]}`` for ``splice_opener_readings.py --redo``, which re-times those
openers inside their new spans and replaces their shard readings. Dry run by default.

Examples::

    python3 scripts/backfills/clamp_opener_spans.py --bucket prod --redo redo.json
    python3 scripts/backfills/clamp_opener_spans.py --bucket prod --redo redo.json --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from splice_opener_readings import _setup_paths_and_env

OPENERS = ("Isti'adha", "Basmala")


def first_verse_starts(entries: list[dict], specials: frozenset[str]) -> dict[int, int]:
    """Each chapter's earliest verse segment start (labelled special rows excluded)."""
    out: dict[int, int] = {}
    for entry in entries:
        chapter = int(str(entry["ref"]).split(":")[0])
        for seg in entry.get("segments", []):
            if ":" not in (seg.get("matched_ref") or "") or seg["matched_ref"] in specials:
                continue
            out[chapter] = min(out.get(chapter, seg["time_start"]), seg["time_start"])
    return out


def clamp_chapter(items: list[dict], ayah1_start: int | None) -> list[str]:
    """End each pre opener at the next pre opener's start or ``ayah1_start``; return notes."""
    pre = sorted(
        (e for e in items if e["position"] == "pre" and e["type"] in OPENERS),
        key=lambda e: e["start_ms"],
    )
    notes = []
    for i, entry in enumerate(pre):
        if entry["source"] == "fatiha_verse":
            continue
        limits = [p["start_ms"] for p in pre[i + 1 :]]
        if ayah1_start is not None:
            limits.append(ayah1_start)
        limit = min(limits, default=None)
        if limit is None or entry["end_ms"] <= limit:
            continue
        if limit <= entry["start_ms"]:
            notes.append(f"!! {entry['type']}@{entry['start_ms']} starts at/after {limit}; left as is")
            continue
        notes.append(f"{entry['type']} {entry['start_ms']}-{entry['end_ms']} -> {limit}")
        entry.update(end_ms=limit, clipped=True, words=[])
    return notes


def process(slug: str, *, apply: bool) -> list[int]:
    from qua_shared.schemas.bucket.specials import SpecialsDoc
    from services.segments import specials as specials_service
    from services.segments.specials_doc import SPECIAL_NAMES
    from services.storage import data_dir
    from services.timing import aligner_timing

    with specials_service.WRITE_LOCK:
        doc = specials_service.read_doc(slug)
        if doc is None:
            return []
        raw = doc.model_dump(mode="json")
        starts = first_verse_starts(aligner_timing.read_detailed(slug)["entries"], SPECIAL_NAMES)
        changed = []
        for ch, items in raw["chapters"].items():
            notes = clamp_chapter(items, starts.get(int(ch)))
            if any(not n.startswith("!!") for n in notes):
                changed.append(int(ch))
            for note in notes:
                print(f"  {slug} ch{ch}: {note}")
        if changed and apply:
            data_dir.write_specials_doc(slug, SpecialsDoc.model_validate(raw).model_dump(mode="json"))
    return sorted(changed)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--bucket", choices=("dev", "prod"), required=True)
    ap.add_argument("--slug", action="append", help="repeatable; default every reciter")
    ap.add_argument("--redo", type=Path, required=True, help="write {slug: [chapters]} here")
    ap.add_argument("--apply", action="store_true", help="write specials.json (default dry run)")
    args = ap.parse_args()
    _setup_paths_and_env(args.bucket)
    from services.storage import data_dir

    redo = {}
    for slug in args.slug or sorted(data_dir.list_slugs()):
        chapters = process(slug, apply=args.apply)
        if chapters:
            redo[slug] = chapters
    args.redo.write_text(json.dumps(redo, indent=1), "utf-8")
    total = sum(map(len, redo.values()))
    print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {total} chapters over {len(redo)} reciters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
