"""Score every opener's voice against one reference pool per reciter, on Katana.

    python voice_job.py <bodies dir> <out dir> [--workers N] [--shard i/n]

The pool is verse clips from chapters spread across the reciter's recordings
(``POOL_PER_CHAPTER`` of each exported chapter's ``voice_refs``, ``POOL_CHAPTERS``
chapters), so every opener — and Al-Fatiha's 1:1 Basmala, for checking against the
maintainers' calls — is scored against the same reference. Writes
``<out>/<slug>.json``: ``{"pool": n, "fatiha": clip, "openers": {chapter: {start_ms: clip}}}``,
each clip ``{type, dur_ms, z, groups, mfcc}`` (``mfcc`` = MFCC means, to find identical clips).
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from opener_job import SR, _fs, _samples

POOL_CHAPTERS = 24
POOL_PER_CHAPTER = 3
PAD_MS = 2_000


def _fatiha_span(slug: str) -> list[int] | None:
    from qua_timing_batch.retime import inputs

    try:
        doc = json.loads(_fs().read_text(inputs.reciter_path(slug, "specials.json"), encoding="utf-8"))
    except FileNotFoundError:
        return None
    rows = [e for e in doc.get("chapters", {}).get("1", []) if e.get("source") == "fatiha_verse"]
    return [rows[0]["start_ms"], rows[0]["end_ms"]] if rows else None


def _pool_chapters(bodies: list[dict]) -> set[int]:
    chapters = sorted(b["chapter"] for b in bodies if b["voice_refs"])
    if len(chapters) <= POOL_CHAPTERS:
        return set(chapters)
    step = len(chapters) / POOL_CHAPTERS
    return {chapters[int(i * step)] for i in range(POOL_CHAPTERS)}


def score_reciter(slug: str, bodies: list[dict], work: str) -> dict:
    from qua_sdk.integrations import basmala_voice as V

    pool_chs = _pool_chapters(bodies)
    fatiha = _fatiha_span(slug)
    pool, clips = [], []  # clips: (chapter or None, start key, type, samples)
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        by_chapter = {b["chapter"]: b for b in bodies}
        chapters = sorted(set(by_chapter) | ({1} if fatiha else set()))
        for ch in chapters:
            body = by_chapter.get(ch)
            segs = [s for e in body["entries"] for s in e["segments"]] if body else []
            refs = body["voice_refs"][:POOL_PER_CHAPTER] if body and ch in pool_chs else []
            spans = [[s["time_start"], s["time_end"]] for s in segs] + refs
            if ch == 1 and fatiha:
                spans.append(fatiha)
            if not spans:
                continue
            ref = str(body["entries"][0]["ref"]) if body else "1"
            y = _samples(slug, ref, max(e for _, e in spans) + PAD_MS, Path(work))

            def cut(s, e):
                return np.asarray(y[s * SR // 1000 : e * SR // 1000], dtype=np.float32)

            pool += [V.clip_features(c) for s, e in refs if len(c := cut(s, e)) > V.MIN_CLIP_S * SR]
            for s in segs:
                clips.append((ch, str(s["time_start"]), s["matched_ref"], cut(s["time_start"], s["time_end"])))
            if ch == 1 and fatiha:
                clips.append((None, str(fatiha[0]), "Basmala", cut(*fatiha)))
        out = {"pool": len(pool), "fatiha": None, "openers": {}}
        for ch, key, kind, y in clips:
            rec = {"type": kind, "dur_ms": round(len(y) * 1000 / SR), "z": None, "groups": None, "mfcc": None}
            if len(pool) >= V.MIN_REFERENCES and len(y) >= V.MIN_CLIP_S * SR:
                f = V.clip_features(y)
                groups = V.group_z(f, pool)
                z = V.score(groups)
                rec.update(
                    z=round(z, 4) if np.isfinite(z) else None,
                    groups={k: round(v, 4) for k, v in groups.items() if np.isfinite(v)},
                    mfcc=[round(float(v), 3) for v in f["mfcc_mean"]],
                )
            if ch is None:
                out["fatiha"] = rec
            else:
                out["openers"].setdefault(str(ch), {})[key] = rec
    return out


def _task(slug: str, bodies: list[dict], out: str, work: str) -> str:
    try:
        result = score_reciter(slug, bodies, work)
    except Exception:
        return f"FAIL {slug}\n{traceback.format_exc()}"
    target = Path(out) / f"{slug}.json"
    part = target.with_suffix(".part")
    part.write_text(json.dumps(result), encoding="utf-8")
    os.replace(part, target)
    n = sum(len(v) for v in result["openers"].values())
    return f"ok {slug} pool={result['pool']} openers={n} fatiha_z={(result['fatiha'] or {}).get('z')}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bodies", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--shard", default="0/1")
    args = ap.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))
    args.out.mkdir(parents=True, exist_ok=True)
    work = Path(os.environ.get("TMPDIR") or "/tmp") / "voice_audio"
    work.mkdir(parents=True, exist_ok=True)
    todo = [
        (p.stem, json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(args.bodies.glob("*.json"))[shard::shards]
        if not (args.out / f"{p.stem}.json").exists()
    ]
    print(f"[voice] {len(todo)} reciter(s)", flush=True)
    failed = 0
    with ProcessPoolExecutor(args.workers) as pool:
        futures = [pool.submit(_task, s, b, str(args.out), str(work)) for s, b in todo]
        for done, fut in enumerate(as_completed(futures), start=1):
            line = fut.result()
            failed += line.startswith("FAIL")
            print(f"[{done}/{len(todo)}] {line}", flush=True)
    print(f"[voice] done, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
