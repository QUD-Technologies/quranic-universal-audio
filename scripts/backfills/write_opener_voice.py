"""Write each recorded opener's voice verdict to ``specials.json`` from the Katana runs.

Every Isti'adha / Basmala gets the check Al-Fatiha's 1:1 Basmala gets: its voice
features against its own chapter's first verses (the per-chapter z of
``katana_openers/opener_job.py``). A recording that splices one stock clip onto
every chapter would otherwise get split verdicts, since each chapter's references
differ, so openers of one type that are the same audio — durations within
``DUR_TOL_MS`` and MFCC means within ``MFCC_TOL`` (``katana_openers/voice_job.py``
fingerprints) — share one verdict, decided on the median of their z. A maintainer's
call (``voice_confirmed``) is never touched; each reciter that changed is reported
through ``ts-refreshed`` so its GH release goes stale.

Inputs: ``--verdicts`` (``{slug: {chapter: {start_ms: {verdict, z}}}}``, the opener
job's ``voice`` per reply) and ``--fingerprints DIR`` (``<slug>.json`` per reciter).

Examples::

    python scripts/backfills/write_opener_voice.py --verdicts voice_verdicts.json --fingerprints voice/
    python scripts/backfills/write_opener_voice.py --verdicts voice_verdicts.json --fingerprints voice/ --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from pathlib import Path

log = logging.getLogger("write_opener_voice")

#: ``qua_sdk.integrations.basmala_voice`` thresholds (the SDK is not an Inspector dependency).
SAME_BELOW = 1.7
DIFFERENT_ABOVE = 2.3
#: Two clips this close in length and MFCC means are one recording reused.
DUR_TOL_MS = 60
MFCC_TOL = 6.0


def decide(z: float) -> str:
    if z < SAME_BELOW:
        return "same"
    if z > DIFFERENT_ABOVE:
        return "different"
    return "needs_confirmation"


def same_clip(a: dict, b: dict) -> bool:
    if a["type"] != b["type"] or not a.get("mfcc") or not b.get("mfcc"):
        return False
    if abs(a["dur_ms"] - b["dur_ms"]) > DUR_TOL_MS:
        return False
    dist = sum((x - y) ** 2 for x, y in zip(a["mfcc"], b["mfcc"], strict=True)) ** 0.5
    return dist <= MFCC_TOL


def clip_groups(clips: dict[tuple[str, str], dict]) -> list[list[tuple[str, str]]]:
    """``clips`` (keyed ``(chapter, start_ms)``) partitioned into same-audio groups."""
    keys = list(clips)
    parent = {k: k for k in keys}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if same_clip(clips[a], clips[b]):
                parent[root(a)] = root(b)
    groups: dict = {}
    for k in keys:
        groups.setdefault(root(k), []).append(k)
    return list(groups.values())


def consensus(verdicts: dict, prints: dict) -> dict[int, dict[str, dict]]:
    """One reciter's final ``{chapter: {start_ms: {verdict, z}}}``: per-chapter z, with each
    same-audio group sharing the median z of its members."""
    zs = {
        (ch, start): v["z"]
        for ch, by_start in verdicts.items()
        for start, v in by_start.items()
        if v.get("z") is not None
    }
    clips = {
        (ch, start): clip
        for ch, by_start in prints.get("openers", {}).items()
        for start, clip in by_start.items()
        if (ch, start) in zs
    }
    out: dict[int, dict[str, dict]] = {}
    for group in clip_groups(clips):
        z = statistics.median(zs[k] for k in group)
        for ch, start in group:
            out.setdefault(int(ch), {})[start] = {"verdict": decide(z), "z": round(z, 4)}
    for (ch, start), z in zs.items():
        out.setdefault(int(ch), {}).setdefault(start, {"verdict": decide(z), "z": z})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--verdicts", type=Path, required=True)
    ap.add_argument("--fingerprints", type=Path, required=True)
    ap.add_argument("--bucket", choices=("dev", "prod"), default="prod")
    ap.add_argument("--slug", nargs="+", help="only these reciters")
    ap.add_argument("--execute", action="store_true", help="Write. Without it only count.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    verdicts = json.loads(args.verdicts.read_text("utf-8"))
    slugs = args.slug or sorted(verdicts)

    import splice_opener_readings as S

    if args.execute:
        S._setup_paths_and_env(args.bucket)
        from qua_shared.inspector_notify import notify_ts_refreshed
        from services.timing import specials_timing
    failed = 0
    for slug in slugs:
        path = args.fingerprints / f"{slug}.json"
        prints = json.loads(path.read_text("utf-8")) if path.is_file() else {}
        final = consensus(verdicts.get(slug, {}), prints)
        counts: dict[str, int] = {}
        for by_start in final.values():
            for v in by_start.values():
                counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        if not args.execute:
            log.info("%s: %s", slug, counts)
            continue
        changed = S.write_voice(specials_timing, slug, {ch: v for ch, v in final.items()})
        if changed and not notify_ts_refreshed(S._INSPECTORS[args.bucket], slug, reason="opener_voice"):
            log.error("%s: verdicts written but the ts-refreshed report was not accepted", slug)
            failed += 1
        log.info("%s: %s, %d changed", slug, counts, changed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
