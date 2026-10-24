"""Re-derive ``source_ref`` / ``projection_support`` on a projected delivery's segments.

Both fields are written once, when a segment's ref is set (align, promote, an edit),
and nothing refreshes them after. Deliveries aligned before the SDK projected a
cross-verse target range across the verse boundary still carry the truncated Hafs
span: Warsh ``7:136:12-7:137:2`` was stamped ``7:137:12-7:137:18`` where the edition
now gives ``7:137:12-7:137:20``. The timestamps engine times only the Hafs words of
``source_ref``, so the tail words reach no shard and ``cut_release`` gates their
verses as incomplete.

This re-stamps every segment through ``stamp_projection`` (the same call the save
path makes) and prints the chapters whose stamps moved; those need a TS regen
(``admin_ts_job.py launch <slug> --chapters ...``), whose timing pass re-times any
segment whose ref changed. Hafs deliveries are skipped: they carry no provenance.
Dry run by default; restart the Space after ``--apply`` so its in-memory
detailed.json is reloaded.

Examples::

    python3 scripts/backfills/restamp_source_refs.py --bucket prod
    python3 scripts/backfills/restamp_source_refs.py --bucket prod --slug ahmed_deban_qalon_mp3quran --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Callable

from splice_opener_readings import _setup_paths_and_env

_FIELDS = ("source_ref", "projection_support")


def restamp(entries: list[dict], stamp: Callable[[dict], None]) -> dict[int, int]:
    """Stamp every segment in place; return ``{chapter: segments whose stamps moved}``."""
    moved: dict[int, int] = defaultdict(int)
    for entry in entries:
        chapter = int(str(entry["ref"]).split(":")[0])
        for seg in entry.get("segments", []):
            before = tuple(seg.get(f) for f in _FIELDS)
            stamp(seg)
            if tuple(seg.get(f) for f in _FIELDS) != before:
                moved[chapter] += 1
    return dict(moved)


def _projected_slugs() -> list[str]:
    from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
    from services.reference.delivery_edition import sdk_riwayah_for
    from services.storage import data_dir

    return [s for s in sorted(data_dir.list_slugs()) if sdk_riwayah_for(s) != DEFAULT_SDK_RIWAYAH]


def process(slug: str, *, apply: bool) -> dict[int, int]:
    from services.reference.delivery_edition import sdk_riwayah_for
    from services.segments.projection_stamp import stamp_projection
    from services.storage import data_dir

    raw = data_dir.read_detailed_bytes(slug)
    if raw is None:
        raise SystemExit(f"{slug}: no detailed.json")
    doc = json.loads(raw)
    riwayah = sdk_riwayah_for(slug)
    moved = restamp(doc["entries"], lambda seg: stamp_projection(seg, riwayah))
    if moved and apply:
        data_dir.write_detailed_doc(slug, doc)
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--bucket", choices=("dev", "prod"), required=True)
    ap.add_argument("--slug", action="append", help="repeatable; default every projected delivery")
    ap.add_argument("--apply", action="store_true", help="write detailed.json (default dry run)")
    args = ap.parse_args()
    _setup_paths_and_env(args.bucket)

    slugs = args.slug or _projected_slugs()
    print(f"{'APPLY' if args.apply else 'DRY RUN'} on {args.bucket}: {len(slugs)} deliveries")
    for slug in slugs:
        moved = process(slug, apply=args.apply)
        if moved:
            chapters = ",".join(str(c) for c in sorted(moved))
            print(f"{slug}: {sum(moved.values())} segments re-stamped; regen --chapters {chapters}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
