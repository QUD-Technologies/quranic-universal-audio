"""Bench the storage backend on real inspector hot paths.

Measures cold + warm timings for the read/write operations the
inspector actually performs against a real reciter in the dev bucket.

Run:
    INSPECTOR_BUCKET_REPO=QUD-Technologies/quranic-inspector-bucket-dev \\
    python3 scripts/diagnostics/bench_storage.py [--mount /path]

A scratch dir ``reciters/__bench__/`` is created for writes and cleaned at exit.
"""

from __future__ import annotations

import argparse
import gc
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "inspector"))
sys.path.insert(0, str(ROOT))

from inspector.services.hf_bucket import BucketBackend  # noqa: E402

BUCKET = os.environ.get("INSPECTOR_BUCKET_REPO", "QUD-Technologies/quranic-inspector-bucket-dev")
SLUG = "mishary_rashid_al_afasy_mp3quran"
SCRATCH = "reciters/__bench__"


def time_call(fn, *args, **kwargs) -> tuple[float, object]:
    gc.collect()
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000.0, result


def invalidate_hffs():
    try:
        from huggingface_hub import hffs

        hffs.invalidate_cache()
    except Exception:
        pass


def stats(label: str, samples: list[float], extra: str = "") -> None:
    if not samples:
        print(f"  {label:<38}  no samples")
        return
    s = sorted(samples)
    p50 = statistics.median(s)
    p95 = s[min(len(s) - 1, int(0.95 * len(s)))]
    print(
        f"  {label:<38}  n={len(s):<2}  min={min(s):7.1f}  "
        f"p50={p50:7.1f}  p95={p95:7.1f}  max={max(s):7.1f} ms  {extra}"
    )


def bench_reads(b: BucketBackend, n_warm: int = 5) -> None:
    print("\n== READS (cold = fresh hffs cache, warm = repeat) ==")

    targets = [
        ("state/reciter_state.json", "read_json", "store"),
        ("access/inspector_roles.json", "read_json", "store"),
        ("catalog/reciter_catalog.json", "read_json", "store-large"),
        (f"reciters/{SLUG}/segments.json", "read_json", "reciter-md"),
        (f"reciters/{SLUG}/detailed.json", "read_bytes", "reciter-big"),
        (f"reciters/{SLUG}/edit_history.jsonl", "iter_jsonl", "history"),
        (f"reciters/{SLUG}/timestamps/1.json.br", "read_bytes", "ts-chapter"),
    ]

    for path, op, tag in targets:
        cold_samples: list[float] = []
        warm_samples: list[float] = []
        size = None

        def call(op: str = op, path: str = path) -> object:
            if op == "read_bytes":
                return b.read_bytes(path)
            if op == "read_json":
                return b.read_json(path)
            if op == "iter_jsonl":
                return list(b.iter_jsonl(path))
            raise ValueError(op)

        # Cold: invalidate fsspec cache each iteration
        for _ in range(3):
            invalidate_hffs()
            dt, res = time_call(call)
            cold_samples.append(dt)
            if size is None:
                if isinstance(res, (bytes, bytearray)):
                    size = len(res)
                elif isinstance(res, list):
                    size = sum(len(repr(x)) for x in res)
                else:
                    import orjson

                    size = len(orjson.dumps(res))

        # Warm: no invalidation, repeat
        for _ in range(n_warm):
            dt, _ = time_call(call)
            warm_samples.append(dt)

        sz_kb = (size or 0) / 1024.0
        stats(f"{tag:<11} {op:<11} {path[-40:]}", cold_samples, extra=f"[cold, {sz_kb:6.1f} KB]")
        stats(f"{'':<11} {'':<11} {'(warm)':<40}", warm_samples, extra="[warm]")

    # list_dir + exists
    invalidate_hffs()
    samples = []
    for _ in range(3):
        invalidate_hffs()
        dt, _ = time_call(b.list_dir, "reciters")
        samples.append(dt)
    stats("list_dir   list_dir    reciters/", samples, extra="[cold]")

    samples = []
    for _ in range(3):
        dt, _ = time_call(b.exists, f"reciters/{SLUG}/segments.json")
        samples.append(dt)
    stats("exists     exists      segments.json", samples, extra="[mixed]")


def bench_writes(b: BucketBackend, n: int = 3) -> None:
    print("\n== WRITES ==")

    small_path = f"{SCRATCH}/small.json"
    large_path = f"{SCRATCH}/large.json"
    hist_path = f"{SCRATCH}/edit_history.jsonl"

    small = {"x": list(range(100))}
    large = {"rows": [{"i": i, "vals": list(range(20))} for i in range(20_000)]}

    import orjson

    print(f"  small payload: {len(orjson.dumps(small)) / 1024:.1f} KB")
    print(f"  large payload: {len(orjson.dumps(large)) / 1024:.1f} KB")

    s = []
    for _ in range(n):
        dt, _ = time_call(b.write_json_atomic, small_path, small)
        s.append(dt)
    stats("write_json small  (~1 KB)", s)

    s = []
    for _ in range(n):
        dt, _ = time_call(b.write_json_atomic, large_path, large)
        s.append(dt)
    stats("write_json large  (1+ MB)", s)

    # append_jsonl — without mount, this is read-modify-write of whole file.
    # Pre-seed so the read leg is non-trivial.
    for i in range(50):
        b.append_jsonl(hist_path, {"i": i, "data": "x" * 200})
    s = []
    for i in range(n):
        dt, _ = time_call(b.append_jsonl, hist_path, {"i": 1000 + i, "data": "y" * 200})
        s.append(dt)
    stats("append_jsonl (50-row preseeded)", s, extra="[rmw on bucket, in-place on mount]")


def cleanup(b: BucketBackend) -> None:
    try:
        for name in b.list_dir(SCRATCH):
            try:
                b.delete(f"{SCRATCH}/{name}")
            except Exception as e:
                print(f"  cleanup err {name}: {e}")
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mount", default=None, help="path to local hf-mount of the dev bucket (optional)"
    )
    ap.add_argument("--skip-writes", action="store_true")
    ap.add_argument("--warm", type=int, default=5)
    args = ap.parse_args()

    print(f"bucket={BUCKET}")
    print(f"mode={'MOUNTED (' + args.mount + ')' if args.mount else 'NO MOUNT (hffs.cat_file)'}")
    print(f"slug={SLUG}")

    b = BucketBackend(BUCKET, mount=args.mount)

    bench_reads(b, n_warm=args.warm)
    if not args.skip_writes:
        try:
            bench_writes(b)
        finally:
            cleanup(b)


if __name__ == "__main__":
    main()
