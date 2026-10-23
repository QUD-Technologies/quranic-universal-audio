"""Time each chapter's recorded openers, on Katana.

    python opener_job.py <bodies dir> <replies dir> [--workers N] [--threads T]

``<bodies dir>/<slug>.json`` is a list of chapter bodies exported by
``scripts/backfills/splice_opener_readings.py --export`` — the aligner's
``/api/v1/extraction/timing`` request (verse segments emptied, openers in front). Each
chapter runs what the aligner's ``chapter_timing._run`` runs (same head, ``specials``),
reading only the recording's opening bytes. One reply per chapter lands at
``<replies dir>/<slug>/<chapter>.json``; an existing reply is kept, so a rerun resumes.

On Katana (``env.sh``: the ``qua313`` env, a qua snapshot at ``$R/repo`` from
``git archive <qua commit> packages/sdk packages/quran-domain packages/contracts
engines/timing-batch``): copy this folder and the bodies to ``$R``, then one
``qsub -l select=1:ncpus=8:mem=24gb -v OPENER_SHARD=i/n openers.pbs`` per shard
(``voice.pbs`` likewise runs ``voice_job.py``).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import cache
from pathlib import Path

import numpy as np

SR = 16_000
#: First read of a recording; grown x4 until the decoded audio covers what is needed.
PREFIX_BYTES = 2 << 20
#: Audio decoded past the last opener / voice reference.
PAD_MS = 2_000


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@cache
def _aligner():
    from qua_sdk.components.timing.runtimes.neural_head import NeuralHeadAligner, NeuralParams

    return NeuralHeadAligner(), NeuralParams(recitation_riwayah="hafs")


@cache
def _fs():
    from qua_timing_batch.retime import inputs

    return inputs._fs()


def _read(slug: str, ref: str, n: int) -> bytes:
    """The recording's first ``n`` bytes: the bucket copy, else its manifest URL."""
    import requests
    from qua_timing_batch.retime import inputs

    src = inputs.reciter_path(slug, f"audio/{ref}.mp3")
    if _fs().exists(src):
        with _fs().open(src, "rb") as handle:
            return handle.read(n)
    url = inputs._manifest_url(_fs(), slug, ref)
    resp = requests.get(url, headers={"Range": f"bytes=0-{n - 1}"}, timeout=120)
    resp.raise_for_status()
    return resp.content[:n]


def _samples(slug: str, ref: str, need_ms: int, work: Path) -> np.ndarray:
    """The recording's opening 16 kHz samples, at least ``need_ms`` of them when it is that long."""
    from qua_timing_batch.audio.decode import decode

    path = work / f"{slug}__{ref}.mp3"
    n = PREFIX_BYTES
    while True:
        data = _read(slug, ref, n)
        path.write_bytes(data)
        samples = decode(path, SR)
        if len(samples) * 1000 >= need_ms * SR or len(data) < n:
            path.unlink(missing_ok=True)
            return samples
        n *= 4


def time_chapter(slug: str, body: dict, work: str) -> dict:
    """One chapter body → the aligner's reply fields the splice reads."""
    from qua_sdk.integrations import faces as F
    from qua_sdk.integrations import segment_times as ST
    from qua_sdk.integrations import shard_coverage
    from qua_sdk.integrations.shard_build import shard_bytes

    t0 = time.perf_counter()
    aligner, p = _aligner()
    entries = body["entries"]
    doc = ST.decode(base64.b64decode(body["times"])) if body["times"] else ST.empty(body["chapter"])
    for entry in entries:
        segs = entry["segments"]
        if not segs:
            continue
        need = max(s["time_end"] for s in segs) + PAD_MS
        samples = _samples(slug, str(entry["ref"]), need, Path(work))
        ST.time_recording(entry, samples, doc, aligner, p, specials=True)
    ST.prune(doc, entries, specials=True)
    if body.get("delivery_lazim_ms") is not None:
        doc["_meta"]["lazim_p5_ms"] = F.p5(body["delivery_lazim_ms"])
    skipped: list[dict] = []
    shards, failed = ST.chapter_shards(
        entries,
        doc,
        audio_category=body["audio_category"],
        riwayah=body["riwayah"],
        specials=True,
        skipped=skipped,
    )
    coverage = shard_coverage.coverage(doc, shards)
    return {
        "times": _b64(ST.encode(doc)),
        "shards": {str(ch): _b64(shard_bytes(s)) for ch, s in shards.items()},
        "failed_segments": failed,
        "untimed": shard_coverage.untimed(coverage),
        "specials_skipped": [one["segment_uid"] for one in skipped],
        "seconds": round(time.perf_counter() - t0, 1),
    }


def _task(slug: str, body: dict, out: str, work: str) -> str:
    target = Path(out) / slug / f"{body['chapter']}.json"
    try:
        reply = time_chapter(slug, body, work)
    except Exception:
        return f"FAIL {slug} ch{body['chapter']}\n{traceback.format_exc()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    part.write_text(json.dumps(reply), encoding="utf-8")
    os.replace(part, target)
    return f"ok {slug} ch{body['chapter']} {reply['seconds']}s"


def _init(threads: int) -> None:
    import torch

    torch.set_num_threads(threads)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bodies", type=Path)
    ap.add_argument("replies", type=Path)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--shard", default="0/1", help="i/n: this job's share of the reciters")
    args = ap.parse_args()
    shard, shards = (int(x) for x in args.shard.split("/"))
    work = Path(os.environ.get("TMPDIR") or "/tmp") / "opener_audio"
    work.mkdir(parents=True, exist_ok=True)
    todo = []
    for path in sorted(args.bodies.glob("*.json"))[shard::shards]:
        slug = path.stem
        for body in json.loads(path.read_text(encoding="utf-8")):
            if not (args.replies / slug / f"{body['chapter']}.json").exists():
                todo.append((slug, body))
    print(f"[openers] {len(todo)} chapter(s) to time", flush=True)
    failed = 0
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.threads,)) as pool:
        futures = [pool.submit(_task, s, b, str(args.replies), str(work)) for s, b in todo]
        for done, fut in enumerate(as_completed(futures), start=1):
            line = fut.result()
            failed += line.startswith("FAIL")
            print(f"[{done}/{len(todo)}] {line}", flush=True)
    print(f"[openers] done, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
