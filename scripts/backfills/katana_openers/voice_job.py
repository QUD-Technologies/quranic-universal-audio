"""Score every recorded opener's voice and fingerprint it, on Katana.

    python voice_job.py <bodies dir> <out dir> [--workers N] [--shard i/n]

For each exported chapter body (``splice_opener_readings.py --export``), every opener
is scored with ``qua_sdk.integrations.basmala_voice``'s features against the chapter's
own verse clips (``voice_refs``) — Al-Fatiha's 1:1 check, applied per chapter — with no
length floor (a short clip is padded). Each clip also gets a fingerprint (duration +
MFCC means) so ``write_opener_voice.py`` can find reused audio. Al-Fatiha's 1:1 Basmala
(read from the reciter's ``specials.json``) is fingerprinted too.

Writes ``<out>/<slug>.json``:
``{"fatiha": clip | null, "openers": {chapter: {start_ms: clip}}}``, each clip
``{type, dur_ms, z, mfcc}``.
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

PAD_MS = 2_000
#: Shortest clip the features take (pyin frames); a shorter one is zero-padded.
MIN_SAMPLES = 4_096


def _fatiha_span(slug: str) -> list[int] | None:
    from qua_timing_batch.retime import inputs

    try:
        doc = json.loads(
            _fs().read_text(inputs.reciter_path(slug, "specials.json"), encoding="utf-8")
        )
    except FileNotFoundError:
        return None
    rows = [e for e in doc.get("chapters", {}).get("1", []) if e.get("source") == "fatiha_verse"]
    return [rows[0]["start_ms"], rows[0]["end_ms"]] if rows else None


def _cut(y: np.ndarray, start_ms: int, end_ms: int) -> np.ndarray:
    clip = np.asarray(y[start_ms * SR // 1000 : end_ms * SR // 1000], dtype=np.float32)
    return np.pad(clip, (0, max(0, MIN_SAMPLES - len(clip))))


def _clip(V, y: np.ndarray, kind: str, refs: list) -> dict:
    f = V.clip_features(y)
    z = V.score(V.group_z(f, refs)) if len(refs) >= V.MIN_REFERENCES else float("nan")
    return {
        "type": kind,
        "dur_ms": round(len(y) * 1000 / SR),
        "z": round(z, 4) if np.isfinite(z) else None,
        "mfcc": [round(float(v), 3) for v in f["mfcc_mean"]],
    }


def score_reciter(slug: str, bodies: list[dict], work: str) -> dict:
    from qua_sdk.integrations import basmala_voice as V

    fatiha = _fatiha_span(slug)
    out: dict = {"fatiha": None, "openers": {}}
    by_chapter = {b["chapter"]: b for b in bodies}
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        for ch in sorted(set(by_chapter) | ({1} if fatiha else set())):
            body = by_chapter.get(ch)
            segs = [s for e in body["entries"] for s in e["segments"]] if body else []
            refs = body["voice_refs"] if body else []
            spans = [[s["time_start"], s["time_end"]] for s in segs] + refs
            if ch == 1 and fatiha:
                spans.append(fatiha)
            ref = str(body["entries"][0]["ref"]) if body else "1"
            y = _samples(slug, ref, max(e for _, e in spans) + PAD_MS, Path(work))
            ref_feats = [V.clip_features(_cut(y, s, e)) for s, e in refs]
            for s in segs:
                clip = _cut(y, s["time_start"], s["time_end"])
                out["openers"].setdefault(str(ch), {})[str(s["time_start"])] = _clip(
                    V, clip, s["matched_ref"], ref_feats
                )
            if ch == 1 and fatiha:
                out["fatiha"] = _clip(V, _cut(y, *fatiha), "Basmala", ref_feats)
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
    return f"ok {slug} openers={n} fatiha={'yes' if result['fatiha'] else 'no'}"


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
