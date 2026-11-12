"""Diff mp3quran's live catalog against our mp3quran deliveries.

mp3quran publishes its whole catalog as one JSON (``/api/v3/reciters``): every reciter with
its sets (moshaf), each a CDN folder (``server``) plus its surah list. This matches each set
to our delivery whose manifest chapter URLs live in that folder, and reports:

* ``new``      — a live set no delivery points at (a reciter or moshaf we never registered);
* ``coverage`` — a matched set whose surah list differs from our manifest's chapters;
* ``gone``     — a delivery whose folder no longer appears in the API.

Matching is by folder URL, so spelling differences between their names and ours don't
matter. ``recent_date`` (from ``/api/v3/recent_reads``) and the reciter ``date`` show when
mp3quran last touched each reciter. Read-only: two API requests, the bucket state DB and one
manifest read per delivery.

  python scripts/diagnostics/mp3quran_catalog_diff.py --bucket prod
  python scripts/diagnostics/mp3quran_catalog_diff.py --bucket prod --json mq_diff.json
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bucket"))
import _bootstrap as bs  # noqa: E402

API = "https://mp3quran.net/api/v3/{}?language=eng"
USER_AGENT = "Mozilla/5.0 (quranic-universal-audio catalog diff)"
HTTP_TIMEOUT_S = 90
MANIFEST_WORKERS = 16


def api(endpoint: str) -> dict:
    req = urllib.request.Request(API.format(endpoint), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        return json.load(r)


def our_slugs(fs, bucket: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "inspector.db"
        db.write_bytes(bs.rl(fs.cat_file, bs.abs_path(bucket, "db/inspector.db")))
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT slug FROM deliveries WHERE channel = 'mp3quran' ORDER BY slug"
            ).fetchall()
        finally:
            conn.close()
    return [r[0] for r in rows]


def our_chapters(fs, bucket: str, slug: str) -> dict[str, str]:
    path = bs.abs_path(bucket, f"catalog/audio_manifest/{slug}.json")
    doc = json.loads(bs.rl(fs.cat_file, path))
    return {k: c["url"] for k, c in doc.get("chapters", {}).items()}


def folder(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[0] if url.endswith(".mp3") else url.rstrip("/")


def diff(reciters: list[dict], recent: dict[int, str], ours: dict[str, dict[str, str]]) -> dict:
    by_folder: dict[str, str] = {}
    for slug, chapters in ours.items():
        for url in chapters.values():
            by_folder.setdefault(folder(url), slug)
    live = set()
    new, coverage = [], []
    for r in reciters:
        for m in r["moshaf"]:
            key = folder(m["server"])
            live.add(key)
            surahs = sorted(int(x) for x in m["surah_list"].split(",") if x)
            row = {"reciter": r["name"], "set": m["name"], "server": m["server"],
                   "surahs": len(surahs), "reciter_date": (r.get("date") or "")[:10],
                   "recent_date": (recent.get(r["id"]) or "")[:10]}
            slug = by_folder.get(key)
            if slug is None:
                new.append(row)
                continue
            have = {int(k) for k in ours[slug] if k.isdigit()}
            if have != set(surahs):
                coverage.append({**row, "slug": slug, "ours": len(have),
                                 "added_upstream": sorted(set(surahs) - have),
                                 "removed_upstream": sorted(have - set(surahs))})
    gone = [s for s, ch in ours.items() if not ({folder(u) for u in ch.values()} & live)]
    new.sort(key=lambda x: (x["recent_date"], x["reciter_date"]), reverse=True)
    return {"new": new, "coverage": coverage, "gone": gone}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    bs.add_bucket_args(p)
    p.add_argument("--json", type=Path, help="write the full report here")
    a = p.parse_args()

    fs, bucket = bs.resolve(a)
    slugs = our_slugs(fs, bucket)
    with cf.ThreadPoolExecutor(MANIFEST_WORKERS) as ex:
        ours = dict(zip(slugs, ex.map(lambda s: our_chapters(fs, bucket, s), slugs)))
    recent = {r["id"]: r.get("recent_date") for r in api("recent_reads")["reads"]}
    report = diff(api("reciters")["reciters"], recent, ours)

    print(f"{len(ours)} mp3quran deliveries; new={len(report['new'])} "
          f"coverage={len(report['coverage'])} gone={len(report['gone'])}")
    for x in report["new"]:
        print(f"NEW  {x['recent_date'] or '-':10} {x['reciter']} | {x['set']} | "
              f"{x['surahs']} | {x['server']}")
    for x in report["coverage"]:
        print(f"COV  {x['slug']}: ours {x['ours']} -> live {x['surahs']} "
              f"+{x['added_upstream']} -{x['removed_upstream']}")
    for s in report["gone"]:
        print(f"GONE {s}")
    if a.json:
        a.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
