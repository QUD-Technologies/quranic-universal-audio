#!/usr/bin/env python3
"""Restamp a local tree of Hafs native shards from the 1405 to the 1421 waqf marks.

Fallback for shards the mass re-time does not rebuild. quranic-phonemizer 3.0.1
reads Hafs with the 1421 Madinah printing's waqf marks (the edition Digital
Khatt writes); shards built before it carry the 1405 marks. The two differ on
352 words, and only in what a shard renders: stop-sign text, the paired-stop
``exclusive_group``, animation-token character ids and the documents digest.
Words, sounds, rules, boundary states and every timing are identical, so a
reading is re-rendered, not re-timed.

Per reading that covers one of the 352 words:

1. re-render under 1405 and require it to equal the stored render exactly
   (guards against producer drift; the whole chapter is refused otherwise);
2. re-render under 1421 and require every difference to be one of the fields
   above;
3. replace ``render`` and keep ``timing`` and ``parts`` byte for byte.

``_meta`` gains ``stop_edition: "1421"`` and the producer version. Each output
shard passes ``audit_v13_document`` and the deterministic writer.

Runs in the qua workspace environment (qua_sdk with STOP_EDITION, phonemizer
3.0.1). Like ``migrate_timestamps_v13.py`` it never touches a bucket: fetch a
tree with ``scripts/bucket/bucket_cp.py``, run this, review the report, then
upload with the usual bucket tooling. Dry-run unless ``--output`` is given.

Usage:
    python scripts/migrations/restamp_stop_edition.py --input <tree> --report r.json
    python scripts/migrations/restamp_stop_edition.py --input <tree> --output <tree2>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import brotli
import orjson

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from qua_sdk.integrations import native, phonemizer  # noqa: E402
from qua_sdk.integrations.cells_codec import encode_render  # noqa: E402

from qua_shared.timestamps_shards import shard_profile, write_validated_shard  # noqa: E402

OLD, NEW = "1405", "1421"
#: Render differences a mark change may cause; anything else refuses the chapter.
ALLOWED = {"b.stop_text", "b.exclusive_group", "a.character_ids", "m[2]"}


class RestampError(ValueError):
    pass


def overlay_refs() -> set[str]:
    from quranic_phonemizer.riwayat.hafs.resources import STOP_OVERLAY_DIR

    return set(json.loads((STOP_OVERLAY_DIR / f"{NEW}.json").read_text(encoding="utf-8")))


def _use_edition(edition: str) -> None:
    phonemizer.STOP_EDITION = edition
    for cached in (native._hafs, native._pen, native.analyse_native):
        cached.cache_clear()


def render(edition: str, ref: str) -> dict:
    _use_edition(edition)
    reading = native.analyse_native(ref, display=True)
    encoded, _ = encode_render(
        reading.analysis_document, reading.source_document, reading.cell_document
    )
    return encoded


def _boundary_diff(old: list, new: list) -> set[str]:
    out = set()
    for x, y in zip(old, new, strict=True):
        if x[0] != y[0]:
            out.add("b.state")
        if [c[:2] + c[3:] for c in x[1]] != [c[:2] + c[3:] for c in y[1]]:
            out.add("b.columns")
        if [c[2] for c in x[1]] != [c[2] for c in y[1]]:
            out.add("b.stop_text")
        if x[2:5] != y[2:5]:
            out.add("b.sounds/bridges/verse_end")
        if x[5] != y[5]:
            out.add("b.exclusive_group")
    return out


def render_diff(old: dict, new: dict) -> set[str]:
    """Name each render field that differs, at the grain ALLOWED speaks."""
    out = set()
    for key in old.keys() | new.keys():
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if key == "m":
            out |= {f"m[{i}]" for i in range(len(a)) if a[i] != b[i]}
        elif key == "b" and len(a) == len(b):
            out |= _boundary_diff(a, b)
        elif key == "a" and len(a) == len(b):
            same = all(x[:2] + x[4:] == y[:2] + y[4:] for x, y in zip(a, b, strict=True))
            out.add("a.character_ids" if same else "a.tokens")
        else:
            out.add(key)
    return out


def _covers(ref: str, targets: set[str]) -> bool:
    from quranic_phonemizer.riwayat.hafs.resources import base_corpus

    return any(str(loc) in targets for loc in base_corpus().locations(ref))


def restamp_reading(reading: dict, targets: set[str], counts: Counter) -> bool:
    stored = reading["render"]
    ref = stored["m"][0]
    if not _covers(ref, targets):
        return False
    if render(OLD, ref) != stored:
        raise RestampError(f"{ref}: stored render is not the 1405 re-render")
    fresh = render(NEW, ref)
    changed = render_diff(stored, fresh)
    if not changed <= ALLOWED:
        raise RestampError(f"{ref}: unexpected render change {sorted(changed - ALLOWED)}")
    counts.update(changed)
    reading["render"] = fresh
    return True


def restamp_shard(doc: dict, targets: set[str]) -> dict:
    """Restamp one shard document in place; return its report row."""
    meta = doc["_meta"]
    if shard_profile(doc) != "native" or meta.get("stop_edition") == NEW:
        return {"status": "skipped", "readings": 0}
    counts: Counter = Counter()
    touched = sum(restamp_reading(r, targets, counts) for r in doc["readings"])
    meta["stop_edition"] = NEW
    meta["phonemizer_version"] = version("quranic-phonemizer")
    return {"status": "restamped", "readings": touched, "fields": dict(counts)}


def _shards(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json.br"))


def _chapter(path: Path) -> int:
    return int(path.name.split(".", 1)[0])


def run(args: argparse.Namespace) -> dict:
    targets = overlay_refs()
    chapters = {int(ref.split(":", 1)[0]) for ref in targets}
    report: dict[str, dict] = {}
    for path in _shards(args.input):
        rel = path.relative_to(args.input)
        if _chapter(path) not in chapters:
            continue
        doc = orjson.loads(brotli.decompress(path.read_bytes()))
        try:
            row = restamp_shard(doc, targets)
        except RestampError as exc:
            report[str(rel)] = {"status": "refused", "reason": str(exc)}
            continue
        report[str(rel)] = row
        if args.output and row["status"] == "restamped":
            target = args.output / rel
            if target.exists() and not args.replace:
                raise SystemExit(f"{target} exists; pass --replace")
            write_validated_shard(target, doc)
    return report


def _summary(report: dict) -> dict:
    status = Counter(row["status"] for row in report.values())
    fields: Counter = Counter()
    for row in report.values():
        fields.update(row.get("fields", {}))
    readings = sum(row.get("readings", 0) for row in report.values())
    return {"shards": dict(status), "readings": readings, "fields": dict(fields)}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="write restamped shards here (default: dry-run)")
    parser.add_argument("--replace", action="store_true", help="overwrite existing output files")
    parser.add_argument("--report", type=Path, help="write the per-shard report as JSON")
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.input.resolve():
        raise SystemExit("--output must differ from --input")
    report = run(args)
    summary = _summary(report)
    if args.report:
        args.report.write_text(json.dumps({"summary": summary, "shards": report}, indent=1) + "\n")
    print(f"{'WRITE' if args.output else 'DRY-RUN'}: {json.dumps(summary, ensure_ascii=False)}")
    refused = [k for k, row in report.items() if row["status"] == "refused"]
    for key in refused:
        print(f"  refused {key}: {report[key]['reason']}")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
