"""Write each recorded opener's voice verdict to ``specials.json`` from the Katana run.

Every Isti'adha / Basmala outside Al-Fatiha's 1:1 gets a plain ``same`` / ``different``
(no ``needs_confirmation``: only the 1:1 goes to review). The verdict is decided per
audio source, not per clip, from ``katana_openers/voice_job.py``'s output — each
opener's z against its own chapter's verses (the 1:1 check) and a fingerprint
(duration + MFCC means). Clips with near-identical fingerprints are one recording
reused (a spliced clip); a source is, in order:

1. the same audio as the reciter's Al-Fatiha 1:1 → that Basmala's answer (the
   maintainer's call, else a ``same`` / ``different`` verdict);
2. the same audio as a clip of another reciter → ``different`` (a stock clip);
3. reused across chapters → ``same`` when the median z of its clips is under the
   reciter's cut, else ``different``;
4. recorded with its chapter (no twin) → ``same`` unless its z is over the cut plus
   ``LIVE_MARGIN`` — a live opener leans the reciter's way: to its own voice, or to
   foreign (under the cut minus the margin) when most of its openers are foreign.

The cut is ``CUT``, moved by the reciter's Al-Fatiha 1:1 when its answer is known: a
reciter whose own Basmala scores z ``f`` sits near ``f``, so the cut rises to
``f + ANCHOR_GAP``; one whose 1:1 is foreign at ``f`` drops it to ``f - ANCHOR_GAP``.

A maintainer's call (``voice_confirmed``) is never touched; each reciter that changed
is reported through ``ts-refreshed`` so its GH release goes stale.

Examples::

    python scripts/backfills/write_opener_voice.py --fingerprints voice2/
    python scripts/backfills/write_opener_voice.py --fingerprints voice2/ --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from pathlib import Path

log = logging.getLogger("write_opener_voice")

#: The z under which a source is the reciter's own voice (between ``basmala_voice``'s 1.7 / 2.3).
CUT = 2.0
#: How far the cut sits from the Al-Fatiha 1:1's z when its answer is known.
ANCHOR_GAP = 0.4
#: The extra room a live opener gets before it is called foreign.
LIVE_MARGIN = 0.3
#: Two clips of one reciter this close in length and MFCC means are one recording.
DUR_TOL_MS = 60
MFCC_TOL = 6.0
#: Tighter, across reciters, so two different recordings never pass as one stock clip.
STOCK_DUR_TOL_MS = 40
STOCK_MFCC_TOL = 3.0
FATIHA = ("fatiha", "")


def _dist(a: dict, b: dict) -> float:
    return sum((x - y) ** 2 for x, y in zip(a["mfcc"], b["mfcc"], strict=True)) ** 0.5


def same_clip(a: dict, b: dict, dur_tol: int = DUR_TOL_MS, mfcc_tol: float = MFCC_TOL) -> bool:
    if a["type"] != b["type"] or not a.get("mfcc") or not b.get("mfcc"):
        return False
    return abs(a["dur_ms"] - b["dur_ms"]) <= dur_tol and _dist(a, b) <= mfcc_tol


def clip_groups(clips: dict) -> list[list]:
    """``clips`` partitioned into same-audio groups (linked transitively)."""
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


def person(slug: str) -> str:
    """The reciter a slug names: its first two tokens, three after a generic second one."""
    tokens = slug.split("_")
    n = 3 if len(tokens) > 2 and tokens[1] in {"al", "abdul", "bin", "abu", "el"} else 2
    return "_".join(tokens[:n])


def stock_clips(prints: dict[str, dict]) -> dict[str, set]:
    """Per reciter, the opener keys whose audio also appears under another reciter."""
    flat = [
        (slug, (ch, start), clip)
        for slug, doc in prints.items()
        for ch, by_start in doc.get("openers", {}).items()
        for start, clip in by_start.items()
        if clip.get("mfcc")
    ]
    flat.sort(key=lambda row: (row[2]["type"], row[2]["dur_ms"]))
    out: dict[str, set] = {}
    for i, (slug, key, clip) in enumerate(flat):
        for other_slug, other_key, other in flat[i + 1 :]:
            if other["type"] != clip["type"] or other["dur_ms"] - clip["dur_ms"] > STOCK_DUR_TOL_MS:
                break
            if person(other_slug) != person(slug) and same_clip(
                clip, other, STOCK_DUR_TOL_MS, STOCK_MFCC_TOL
            ):
                out.setdefault(slug, set()).add(key)
                out.setdefault(other_slug, set()).add(other_key)
    return out


def reciter_cut(fatiha_same: bool | None, fatiha_z: float | None) -> float:
    if fatiha_same is None or fatiha_z is None:
        return CUT
    return max(CUT, fatiha_z + ANCHOR_GAP) if fatiha_same else min(CUT, fatiha_z - ANCHOR_GAP)


def _source_verdict(group, clips, fatiha_same, stock, cut, lean) -> str:
    zs = [clips[k]["z"] for k in group if k != FATIHA and clips[k].get("z") is not None]
    if FATIHA in group and fatiha_same is not None:
        return "same" if fatiha_same else "different"
    if stock & set(group):
        return "different"
    if not zs:
        return ""
    if len([k for k in group if k != FATIHA]) > 1:
        return "same" if statistics.median(zs) < cut else "different"
    return "same" if zs[0] <= cut + lean else "different"


def _decide(clips, fatiha_same, stock, cut, lean) -> dict[int, dict[str, dict]]:
    out: dict[int, dict[str, dict]] = {}
    for group in clip_groups(clips):
        verdict = _source_verdict(group, clips, fatiha_same, stock, cut, lean)
        for key in group:
            if key != FATIHA:
                row = {"verdict": verdict, "z": clips[key].get("z")}
                out.setdefault(int(key[0]), {})[key[1]] = row
    return out


def _rows(out: dict) -> list[dict]:
    return [row for by_start in out.values() for row in by_start.values()]


def decide_reciter(
    doc: dict, fatiha: tuple[bool | None, float | None], stock: set
) -> dict[int, dict[str, dict]]:
    """One reciter's ``{chapter: {start_ms: {verdict, z}}}`` (see the module docstring)."""
    clips = {
        (ch, start): clip
        for ch, by_start in doc.get("openers", {}).items()
        for start, clip in by_start.items()
    }
    if doc.get("fatiha"):
        clips[FATIHA] = doc["fatiha"]
    cut = reciter_cut(*fatiha)
    out = _decide(clips, fatiha[0], stock, cut, LIVE_MARGIN)
    votes = [row["verdict"] for row in _rows(out) if row["verdict"]]
    majority = statistics.mode(votes) if votes else "same"
    if majority == "different":  # a mostly foreign reciter: live takes lean foreign too
        out = _decide(clips, fatiha[0], stock, cut, -LIVE_MARGIN)
    for row in _rows(out):
        row["verdict"] = row["verdict"] or majority  # no verse to score against
    return out


def write_voice(timing, slug: str, by_chapter: dict[int, dict]) -> int:
    """Each opener's voice verdict onto its specials entry; a maintainer's call stays."""
    from qua_shared.schemas.bucket.specials import SpecialsDoc

    if not any(by_chapter.values()):
        return 0
    service, changed = timing.specials_service, 0
    with service.WRITE_LOCK:
        doc = service.read_doc(slug)
        if doc is None:
            return 0
        for chapter, by_start in by_chapter.items():
            for entry in doc.chapters.get(str(chapter), []):
                got = by_start.get(str(entry.start_ms))
                if entry.type not in timing.OPENERS or got is None:
                    continue
                if (entry.voice_verdict, entry.voice_z) != (got["verdict"], got["z"]):
                    entry.voice_verdict, entry.voice_z = got["verdict"], got["z"]
                    changed += 1
        if changed:
            doc = SpecialsDoc.model_validate(doc.model_dump())
            timing.data_dir.write_specials_doc(slug, doc.model_dump(mode="json"))
    return changed


def fatiha_answer(doc) -> tuple[bool | None, float | None]:
    """Al-Fatiha's 1:1 Basmala: own voice? (the maintainer's call, else the verdict) and its z."""
    for entry in doc.chapters.get("1", []) if doc else []:
        if entry.source == "fatiha_verse":
            return entry.same_voice, entry.voice_z
    return None, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--fingerprints", type=Path, required=True, help="voice_job.py output dir")
    ap.add_argument("--bucket", choices=("dev", "prod"), default="prod")
    ap.add_argument("--slug", nargs="+", help="only these reciters")
    ap.add_argument("--execute", action="store_true", help="Write. Without it only count.")
    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    prints = {
        p.stem: json.loads(p.read_text("utf-8")) for p in sorted(args.fingerprints.glob("*.json"))
    }
    stock = stock_clips(prints)

    import splice_opener_readings as S

    S._setup_paths_and_env(args.bucket)
    from qua_shared.inspector_notify import notify_ts_refreshed
    from services.timing import specials_timing

    failed = 0
    for slug in args.slug or sorted(prints):
        final = decide_reciter(
            prints[slug],
            fatiha_answer(specials_timing.specials_service.read_doc(slug)),
            stock.get(slug, set()),
        )
        counts: dict[str, int] = {}
        for by_start in final.values():
            for v in by_start.values():
                counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        if not args.execute:
            log.info("%s: %s, stock=%d", slug, counts, len(stock.get(slug, ())))
            continue
        changed = write_voice(specials_timing, slug, final)
        if changed and not notify_ts_refreshed(
            S._INSPECTORS[args.bucket], slug, reason="opener_voice"
        ):
            log.error("%s: verdicts written but the ts-refreshed report was not accepted", slug)
            failed += 1
        log.info("%s: %s, %d changed", slug, counts, changed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
