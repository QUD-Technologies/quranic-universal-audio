"""Audit catalog audio source URLs for reachability + per-reciter viability.

For every by_surah chapter (by_ayah skipped) classify the stored source URL:

  ok        stored url reachable as-is
  fixable   stored url dead but the canonical archive.org/download form works
            (stale archive node hostname — repairable by a url rewrite)
  dead      neither stored nor canonical reachable (source genuinely gone)

Then per reciter: valid-after-fix = ok + fixable, and a keep/remove signal —
reciters grouped by recitation identity (slug minus channel token) so a mostly-
dead archive delivery can be dropped when a healthy same-recitation delivery
exists on another channel.

Modest concurrency + a retry on timeout/refused: archive.org throttles bursts,
and a throttled timeout must not be miscounted as a dead url.

  python3.11 scripts/diagnostics/audio_url_audit.py --bucket prod \
      --mount <mount> --archive-only --json > /tmp/url_audit.json
  python3.11 scripts/diagnostics/audio_url_audit.py --bucket prod \
      --mount <mount> --quick        # ch1-only health sweep, all reciters
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import re
import sys
import urllib.error
import urllib.request as u
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qua_shared.mp3_probe import canonical_archive_url  # noqa: E402

NODE_RE = re.compile(r"^https?://(?:ia\d+\.us|dn\d+\.ca)\.archive\.org/\d+/items/")
CHANNELS = {"archive", "mp3quran", "tvquran", "qdc", "quranicaudio", "everyayah", "qf"}
TIMEOUT_S = 8


def reachable(url: str, tries: int = 1) -> bool:
    for attempt in range(tries):
        try:
            r = u.urlopen(
                u.Request(url, headers={"Range": "bytes=0-9", "User-Agent": "qua-url-audit"}),
                timeout=TIMEOUT_S,
            )
            return r.status in (200, 206)
        except urllib.error.HTTPError:
            return False  # 404/403 are definitive, no retry
        except Exception:
            if attempt == tries - 1:
                return False
    return False


def identity(slug: str) -> str:
    """Recitation identity = slug minus channel + trailing year/disambiguator tokens.
    Keeps reciter name + riwayah + style so cross-channel duplicates collapse."""
    toks = [t for t in slug.split("_") if t not in CHANNELS]
    while toks and re.fullmatch(r"\d{2,4}k?", toks[-1]):
        toks.pop()
    return "_".join(toks)


def audit_slug(path: str, quick: bool) -> dict:
    """Classify each chapter url. archive staleness is per-item, so probe the
    stored node ONCE (ch1); per chapter test the canonical form (live host →
    fast 206/404, no 15s node timeouts). Non-archive urls are tested directly."""
    slug = os.path.basename(path)[:-5]
    try:
        m = json.load(open(path))
    except Exception as e:  # noqa: BLE001
        return {"slug": slug, "error": repr(e)}
    chapters = m.get("chapters") or {}
    keys = sorted((k for k in chapters if ":" not in k and k.isdigit()), key=int)
    if quick:
        keys = keys[:1]

    urls = {k: ((chapters[k] or {}).get("url") or "") for k in keys}
    is_archive = any(NODE_RE.match(u_) for u_ in urls.values())
    stored_node_ok = False
    if is_archive:
        first = next((urls[k] for k in keys if NODE_RE.match(urls[k])), "")
        stored_node_ok = bool(first) and reachable(first)

    verdict = {"ok": [], "fixable": [], "dead": []}
    for k in keys:
        url = urls[k]
        if not url:
            verdict["dead"].append(k)
            continue
        node = NODE_RE.match(url)
        eff = canonical_archive_url(url) if node else url
        if reachable(eff):
            # node url reachable as-is only when its stored node tested live.
            verdict["ok" if (not node or stored_node_ok) else "fixable"].append(k)
        else:
            verdict["dead"].append(k)
    return {
        "slug": slug,
        "identity": identity(slug),
        "is_archive": is_archive,
        "stored_node_ok": stored_node_ok,
        "n": len(keys),
        "ok": len(verdict["ok"]),
        "fixable": len(verdict["fixable"]),
        "dead": len(verdict["dead"]),
        "dead_chapters": verdict["dead"],
        "fixable_chapters": verdict["fixable"],
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--mount", required=True, help="hf-mount root for manifest reads")
    p.add_argument(
        "--archive-only", action="store_true", help="only reciters with archive node urls"
    )
    p.add_argument(
        "--quick", action="store_true", help="test only ch1 per reciter (fast health sweep)"
    )
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--bucket", default="prod")  # accepted for symmetry; reads come from --mount
    a = p.parse_args()

    man_dir = Path(a.mount) / "catalog" / "audio_manifest"
    paths = []
    for path in sorted(glob.glob(f"{man_dir}/*.json")):
        slug = os.path.basename(path)[:-5]
        if "everyayah" in slug or "byayah" in slug:
            continue
        if a.archive_only:
            try:
                m = json.load(open(path))
            except Exception:  # noqa: BLE001
                continue
            if not any(
                NODE_RE.match((e or {}).get("url") or "")
                for e in (m.get("chapters") or {}).values()
            ):
                continue
        paths.append(path)

    print(f"auditing {len(paths)} reciters (quick={a.quick})", file=sys.stderr)
    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(audit_slug, p, a.quick): p for p in paths}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            results.append(fut.result())
            if i % 10 == 0:
                print(f"  {i}/{len(paths)}", file=sys.stderr)

    ok_res = [r for r in results if not r.get("error")]
    # group by identity for keep/remove
    by_id: dict[str, list] = {}
    for r in ok_res:
        by_id.setdefault(r["identity"], []).append(r)

    if a.json:
        print(json.dumps({"reciters": ok_res, "groups": by_id}, ensure_ascii=False, indent=2))
        return 0

    tot_ok = sum(r["ok"] for r in ok_res)
    tot_fix = sum(r["fixable"] for r in ok_res)
    tot_dead = sum(r["dead"] for r in ok_res)
    print(
        f"\n==== {len(ok_res)} reciters | chapters ok={tot_ok} fixable={tot_fix} dead={tot_dead} ===="
    )

    print("\n-- reciters by health (valid-after-fix / total) --")
    for r in sorted(ok_res, key=lambda r: (r["ok"] + r["fixable"]) / r["n"] if r["n"] else 1):
        valid = r["ok"] + r["fixable"]
        sibs = [s for s in by_id[r["identity"]] if s["slug"] != r["slug"]]
        sib_note = ""
        if sibs:
            best = max(sibs, key=lambda s: s["ok"] + s["fixable"])
            sib_note = f"  ALT[{best['slug']}: valid {best['ok'] + best['fixable']}/{best['n']}]"
        if valid < r["n"]:
            print(
                f"  {r['slug']}: valid {valid}/{r['n']} (ok={r['ok']} fix={r['fixable']} dead={r['dead']})"
                + (f" dead={r['dead_chapters'][:10]}" if r["dead"] else "")
                + sib_note
            )

    print("\n-- removal candidates (mostly dead AND a healthier same-recitation alt exists) --")
    for r in sorted(ok_res, key=lambda r: (r["ok"] + r["fixable"]) / r["n"] if r["n"] else 1):
        valid = r["ok"] + r["fixable"]
        sibs = [s for s in by_id[r["identity"]] if s["slug"] != r["slug"]]
        if not sibs:
            continue
        best = max(sibs, key=lambda s: s["ok"] + s["fixable"])
        if valid / r["n"] < 0.5 and (best["ok"] + best["fixable"]) / best["n"] > valid / r["n"]:
            print(
                f"  drop {r['slug']} (valid {valid}/{r['n']}) -> keep {best['slug']} "
                f"(valid {best['ok'] + best['fixable']}/{best['n']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
