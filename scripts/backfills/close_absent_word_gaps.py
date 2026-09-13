#!/usr/bin/env python3
"""Close the hole an unwritten Hafs word left in word-profile timestamp shards.

Warsh and Qalun do not write Hafs ``57:24:10`` (هُوَ). The Hafs proxy alignment
still timed it, and ``qua_sdk.integrations.word_shards`` used to drop its row
together with its interval, so the surviving neighbours (Warsh ``57:23:9`` and
``57:23:10``) were no longer adjacent and the publish's ``intra_segment_gap``
gate refused the verse. The SDK now folds that span into the previous row;
this script applies the same rule to shards already on the bucket.

Rule, per reading and per part: consecutive rows of ONE part must be adjacent
(forward padding made every real word boundary so), therefore any gap between
them is a dropped word's interval and the earlier row's end moves up to the
later row's start. Gaps BETWEEN parts are never touched — two waṣl-joined
segments keep their real pause.

Every rewritten shard is re-audited (``timestamps_word_audit``) and serialized
by the same deterministic writer the pipeline uses, then uploaded in one Xet
batch per reciter. Native (Hafs) shards are skipped untouched.

Usage:
    python scripts/backfills/close_absent_word_gaps.py --bucket prod            # dry-run
    python scripts/backfills/close_absent_word_gaps.py --bucket prod --apply --yes-prod
    python scripts/backfills/close_absent_word_gaps.py --bucket dev --slug X --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "scripts" / "bucket"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import brotli  # noqa: E402
from _bootstrap import (  # noqa: E402
    abs_path,
    add_bucket_args,
    add_notify_args,
    batch_write,
    confirm_mutation,
    notify_ts_refreshed,
    resolve,
)

from qua_shared.timestamps_shards import shard_profile, validated_brotli_shard  # noqa: E402

#: Slug fragments that name a non-Hafs delivery (catalog slug convention:
#: ``<reciter>_<riwayah>_<source>``). Hafs slugs carry no riwayah token.
NON_HAFS_TOKENS = ("warsh", "qalon", "qalun", "shubah", "shuba")
READ_WORKERS = 8


def close_part_gaps(shard: dict) -> list[str]:
    """Close intra-part gaps IN PLACE; return one ``ref|ref gap_ms`` line per closed gap.

    Only a forward-padded shard qualifies: there every real word boundary is
    already adjacent, so a gap can only be a dropped word. A shard timed with
    any other padding (or a synthetic fixture with no padding meta) is left
    alone — its gaps are its own.
    """
    if (shard.get("_meta") or {}).get("padding") != "forward":
        return []
    closed: list[str] = []
    for reading in shard["readings"]:
        rows = reading["words"]
        for _verse, _t0, _t1, first, count in reading["parts"]:
            for index in range(first, first + count - 1):
                earlier, later = rows[index], rows[index + 1]
                gap = int(later[2]) - int(earlier[3])
                if gap > 0:
                    closed.append(f"{earlier[0]}|{later[0]} {gap}ms")
                    earlier[3] = later[2]
    return closed


def _chapter_paths(fs, bucket_id: str, slug: str) -> list[str]:
    try:
        return sorted(
            p
            for p in fs.ls(abs_path(bucket_id, f"reciters/{slug}/timestamps"), detail=False)
            if p.endswith(".json.br")
        )
    except FileNotFoundError:
        return []


def _read_shard(fs, path: str) -> dict:
    return json.loads(brotli.decompress(fs.cat_file(path)))


def process_slug(fs, bucket_id: str, slug: str, *, apply: bool) -> dict:
    paths = _chapter_paths(fs, bucket_id, slug)
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        shards = list(pool.map(lambda p: _read_shard(fs, p), paths))
    changed: dict[str, bytes] = {}
    closed_all: list[str] = []
    profile = "none"
    for path, shard in zip(paths, shards, strict=True):
        profile = shard_profile(shard)
        if profile != "word":
            break
        closed = close_part_gaps(shard)
        if not closed:
            continue
        chapter = path.rsplit("/", 1)[1].split(".", 1)[0]
        closed_all.extend(f"{chapter}: {line}" for line in closed)
        changed[f"reciters/{slug}/timestamps/{chapter}.json.br"] = validated_brotli_shard(shard)
    if apply and changed:
        batch_write(bucket_id, changed)
    return {
        "slug": slug,
        "profile": profile,
        "chapters": len(paths),
        "changed": sorted(int(k.rsplit("/", 1)[1].split(".", 1)[0]) for k in changed),
        "closed": closed_all,
    }


def _non_hafs_slugs(fs, bucket_id: str) -> list[str]:
    slugs = [p.rsplit("/", 1)[1] for p in fs.ls(abs_path(bucket_id, "reciters"), detail=False)]
    return sorted(s for s in slugs if any(tok in s for tok in NON_HAFS_TOKENS))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    add_bucket_args(parser)
    add_notify_args(parser)
    parser.add_argument("--slug", action="append", help="limit to this slug (repeatable)")
    parser.add_argument("--apply", action="store_true", help="write back (default: dry-run)")
    args = parser.parse_args()
    if args.apply:
        confirm_mutation(args, "rewrite timestamp shards")
    fs, bucket_id = resolve(args)
    slugs = args.slug or _non_hafs_slugs(fs, bucket_id)
    print(f"{'APPLY' if args.apply else 'DRY-RUN'} on {bucket_id}: {len(slugs)} slug(s)")
    total = 0
    for slug in slugs:
        result = process_slug(fs, bucket_id, slug, apply=args.apply)
        total += len(result["closed"])
        print(
            f"- {slug}: profile={result['profile']} chapters={result['chapters']} "
            f"gaps={len(result['closed'])} changed={result['changed']}"
        )
        for line in result["closed"]:
            print(f"    {line}")
        if args.apply and result["changed"]:
            notify_ts_refreshed(args, slug, chapters=result["changed"], reason="absent_word_gap")
    print(f"total gaps {'closed' if args.apply else 'found'}: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
