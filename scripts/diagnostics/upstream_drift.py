"""Report chapters whose upstream source file no longer matches what the bucket holds.

A source CDN can replace a chapter under the same URL (tarteel swapped Husary mujawwad 62
for a different recording); the bucket copy, segments and timestamps keep the original,
while direct CDN playback would serve the new file. This reads, per delivery in the chosen
lifecycle states, the audio manifest's ``size_bytes`` / ``duration_sec`` and the bucket's
``reciters/<slug>/audio/<ch>.mp3`` sizes, then range-reads each upstream URL's head
(``qua_shared.mp3_probe``) for its current size and duration.

Verdicts per chapter:

  ok          upstream size equals the manifest size (else the bucket copy's, when the
              manifest has none)
  recording   size differs and duration differs by more than ``DURATION_TOL_S`` —
              a different recording; playback and timing are out of sync
  reencode    size differs, duration within tolerance, bitrate or sample rate differs
  tag_edit    size differs by under ``TAG_EDIT_BYTES``, same duration — metadata only
  resized     size differs, same duration and format (cause unknown)
  gone        upstream answers 404 / 410
  error       network / parse failure (retried once)

Skipped: YouTube / Google Drive / SoundCloud sources, combined-file chapters (``source_url``
set), and chapters whose url is not http(s). Read-only: one bucket listing per delivery, two
small range requests per chapter.

  python scripts/diagnostics/upstream_drift.py --bucket prod
  python scripts/diagnostics/upstream_drift.py --bucket prod --states released --slug X --slug Y
  python scripts/diagnostics/upstream_drift.py --bucket prod --json out.json --md out.md
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sqlite3
import sys
import tempfile
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "bucket"))
import _bootstrap as bs  # noqa: E402

from qua_shared import mp3_probe  # noqa: E402

DEFAULT_STATES = ("released", "under_review", "awaiting_review", "awaiting_alignment")
SKIP_HOSTS = ("youtube.com", "youtu.be", "drive.google.com", "docs.google.com", "soundcloud.com")
HEAD_BYTES = 16384
DURATION_TOL_S = 2.0
TAG_EDIT_BYTES = 65536
WORKERS = 16
DRIFT = ("recording", "reencode", "resized", "tag_edit", "gone")


def deliveries(fs, bucket: str, states: tuple[str, ...]) -> list[tuple[str, str]]:
    """``(slug, state)`` of every delivery in ``states``, from the bucket's state database."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "inspector.db"
        db.write_bytes(bs.rl(fs.cat_file, bs.abs_path(bucket, "db/inspector.db")))
        conn = sqlite3.connect(db)
        try:
            marks = ",".join("?" * len(states))
            rows = conn.execute(
                f"SELECT slug, state FROM delivery_states WHERE state IN ({marks}) ORDER BY slug",
                states,
            ).fetchall()
        finally:
            conn.close()
    return [(r[0], r[1]) for r in rows]


def bucket_sizes(fs, bucket: str, slug: str) -> dict[str, int]:
    try:
        listing = bs.rl(fs.ls, bs.abs_path(bucket, f"reciters/{slug}/audio"), detail=True)
    except FileNotFoundError:
        return {}
    return {
        Path(e["name"]).stem: int(e["size"])
        for e in listing
        if e.get("type") == "file" and e["name"].endswith(".mp3")
    }


def checkable(entry: dict) -> bool:
    url = entry.get("url") or ""
    return (
        url.startswith(("http://", "https://"))
        and not entry.get("source_url")
        and not any(h in url for h in SKIP_HOSTS)
    )


def probe(url: str) -> dict:
    """Upstream ``size`` / ``duration_s`` / ``kbps`` / ``sr`` from a head read, or an error."""
    for attempt in range(2):
        try:
            buf, total, tag = mp3_probe._fetch(mp3_probe.canonical_archive_url(url), HEAD_BYTES)
            break
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return {"gone": e.code}
            if attempt:
                return {"error": f"http {e.code}"}
        except Exception as e:  # noqa: BLE001 — one retry, then reported
            if attempt:
                return {"error": f"{type(e).__name__}: {e}"[:120]}
    foff, h = mp3_probe._first_frame(buf, mp3_probe._skip_id3(buf))
    if h is None:
        return {"size": total, "error": "no mp3 frame in head"}
    frames = mp3_probe._xing_frames(buf, foff, h)
    if frames:
        duration = frames * h["spf"] / h["sr"]
    elif total:
        duration = (total - tag) * 8 / (h["kbps"] * 1000)
    else:
        duration = None
    return {"size": total, "duration_s": duration, "kbps": h["kbps"], "sr": h["sr"]}


def bucket_head(fs, bucket: str, slug: str, chapter: str) -> dict:
    """``kbps`` / ``sr`` of the bucket copy's first frame (for a manifest without them)."""
    path = bs.abs_path(bucket, f"reciters/{slug}/audio/{chapter}.mp3")
    try:
        tag = mp3_probe._skip_id3(bs.rl(fs.cat_file, path, start=0, end=10))
        buf = bs.rl(fs.cat_file, path, start=tag, end=tag + HEAD_BYTES)
    except Exception:  # noqa: BLE001 — the comparison falls back to size alone
        return {}
    _foff, h = mp3_probe._first_frame(buf, 0)
    return {"kbps": h["kbps"], "sr": h["sr"]} if h else {}


def verdict(entry: dict, held: int | None, up: dict, then: dict) -> str:
    if "gone" in up:
        return "gone"
    if up.get("size") is None:
        return "error"
    expected = entry.get("size_bytes") or held
    if expected is None or up["size"] == expected:
        return "ok" if expected is not None else "error"
    duration, aligned = up.get("duration_s"), entry.get("duration_sec")
    if duration is not None and aligned is not None and abs(duration - aligned) > DURATION_TOL_S:
        return "recording"
    if abs(up["size"] - expected) < TAG_EDIT_BYTES:
        return "tag_edit"
    kbps = then.get("kbps") or entry.get("bitrate_kbps")
    if (kbps and up.get("kbps") and up["kbps"] != kbps) or (
        then.get("sr") and up.get("sr") and up["sr"] != then["sr"]
    ):
        return "reencode"
    return "resized"


def check(fs, bucket: str, slug: str, state: str) -> tuple[list[dict], int]:
    """Chapter rows of one delivery, and how many of its chapters were skipped."""
    try:
        manifest = json.loads(
            bs.rl(fs.cat_file, bs.abs_path(bucket, f"catalog/audio_manifest/{slug}.json"))
        )
    except FileNotFoundError:
        return [{"slug": slug, "state": state, "chapter": None, "verdict": "error",
                 "detail": "no audio manifest", "upstream": {}}], 0  # fmt: skip
    held = bucket_sizes(fs, bucket, slug)
    chapters = manifest.get("chapters") or {}
    numbered = [(k, e) for k, e in chapters.items() if k.isdigit() and isinstance(e, dict)]
    todo = [(k, e) for k, e in numbered if checkable(e)]
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        probes = list(ex.map(lambda ke: probe(ke[1]["url"]), todo))
    rows = []
    for (key, entry), up in zip(todo, probes, strict=True):
        then = {}
        if verdict(entry, held.get(key), up, then) in ("resized", "reencode"):
            then = bucket_head(fs, bucket, slug, key)
        rows.append({
            "slug": slug, "state": state, "chapter": int(key), "url": entry["url"],
            "verdict": verdict(entry, held.get(key), up, then),
            "bucket_kbps": then.get("kbps"), "bucket_sr": then.get("sr"),
            "manifest_size": entry.get("size_bytes"), "bucket_size": held.get(key),
            "manifest_duration_s": entry.get("duration_sec"), "manifest_kbps": entry.get("bitrate_kbps"),
            "upstream": up,
        })  # fmt: skip
    return rows, len(numbered) - len(todo)


def _fmt(kbps: int | None, sr: int | None) -> str:
    return f"{kbps or '?'}k" + (f"/{sr // 1000}kHz" if sr else "")


def markdown(rows: list[dict], skipped: int) -> str:
    by = Counter(r["verdict"] for r in rows)
    out = ["# Upstream drift", "", "| Verdict | Chapters |", "|---|---|"]
    out += [f"| {v} | {by[v]} |" for v in ("ok", *DRIFT, "error") if by[v]]
    out += ["", f"Skipped (non-http / combined-file / YouTube·Drive·SoundCloud): {skipped}"]
    per = defaultdict(Counter)
    for r in rows:
        if r["verdict"] in DRIFT:
            per[(r["slug"], r["state"])][r["verdict"]] += 1
    if per:
        out += ["", "## Deliveries with drift", "", "| Delivery | State | Drift |", "|---|---|---|"]
        for (slug, state), c in sorted(per.items()):
            out.append(
                f"| `{slug}` | {state} | {', '.join(f'{v} {n}' for v, n in c.most_common())} |"
            )
        out += ["", "## Chapters (recording, reencode, gone)", "",
                "| Delivery | Ch | Verdict | Size then → now | Duration then → now | Format then → now |",
                "|---|---|---|---|---|---|"]  # fmt: skip
        for r in sorted(rows, key=lambda r: (r["slug"], r["chapter"] or 0)):
            if r["verdict"] not in ("recording", "reencode", "gone"):
                continue
            up = r["upstream"]
            then = r["manifest_size"] or r["bucket_size"]
            dur = up.get("duration_s")
            out.append(
                f"| `{r['slug']}` | {r['chapter']} | {r['verdict']} | {then} → {up.get('size', up.get('gone'))} | "
                f"{r['manifest_duration_s']} → {round(dur) if dur else '?'} | "
                f"{_fmt(r['bucket_kbps'] or r['manifest_kbps'], r['bucket_sr'])} → "
                f"{_fmt(up.get('kbps'), up.get('sr'))} |"
            )
    errors = [r for r in rows if r["verdict"] == "error"]
    if errors:
        out += ["", f"## Errors ({len(errors)})", ""]
        out += [
            f"- `{r['slug']}` ch{r['chapter']}: {r.get('detail') or r['upstream'].get('error')}"
            for r in errors[:50]
        ]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    bs.add_bucket_args(ap)
    ap.add_argument(
        "--states", default=",".join(DEFAULT_STATES), help="comma-separated lifecycle states"
    )
    ap.add_argument(
        "--slug", action="append", default=[], help="limit to these deliveries (repeatable)"
    )
    ap.add_argument("--json", type=Path, help="write every chapter row as JSON")
    ap.add_argument("--md", type=Path, help="write the markdown report (default: stdout)")
    args = ap.parse_args()
    fs, bucket = bs.resolve(args)
    targets = deliveries(fs, bucket, tuple(s.strip() for s in args.states.split(",") if s.strip()))
    if args.slug:
        targets = [t for t in targets if t[0] in set(args.slug)]
    rows: list[dict] = []
    skipped = 0
    for i, (slug, state) in enumerate(targets, 1):
        part, skip = check(fs, bucket, slug, state)
        rows += part
        skipped += skip
        drift = sum(r["verdict"] in DRIFT for r in part)
        print(
            f"[{i}/{len(targets)}] {slug} ({state}): {len(part)} checked, {drift} drifted",
            file=sys.stderr,
            flush=True,
        )
    report = markdown(rows, skipped)
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        args.md.write_text(report, encoding="utf-8")
    else:
        print(report)
    return 1 if any(r["verdict"] in DRIFT for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
