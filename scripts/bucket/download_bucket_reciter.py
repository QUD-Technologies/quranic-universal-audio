"""Download a reciter folder from a bucket to local disk.

Used as the first step in a download → migrate → re-upload workflow for
legacy reciters that need Migration #5 fixup. By default, audio mp3s are
skipped (typically tens of MBs and don't change during a migration) —
pass ``--include-audio`` to grab them too.

Output mirrors the bucket layout starting at the slug level:

    <out>/
      detailed.json
      segments.json
      edit_history.jsonl
      edit_history_peaks.jsonl
      low_confidence_v2.json
      auto_split_v1.json
      pipeline_meta.json     (if present)
      audio/_done.json
      audio/<ch>.mp3         (only with --include-audio)
      peaks/<ch>.json[.gz]   (everything — legacy + slim mixed)

Usage::

    python3 scripts/bucket/download_bucket_reciter.py \\
        --slug mahmoud_khalil_al_husary_mp3quran --bucket prod \\
        --out /tmp/husary/
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("download_bucket_reciter")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}


def _setup_paths_and_env(bucket: str) -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


def _reciter_exists(backend, slug: str) -> bool:
    """True when ``reciters/<slug>/detailed.json`` is present on the bucket."""
    try:
        backend.read_bytes(f"reciters/{slug}/detailed.json")
        return True
    except Exception:
        return False


def _safe_list(backend, prefix: str) -> list[str]:
    try:
        return backend.list_dir(prefix) or []
    except Exception:
        return []


def _download_file(backend, src: str, dst: Path) -> bool:
    try:
        data = backend.read_bytes(src)
    except Exception as e:
        log.info("skip %s: %s", src, e)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return True


def download(backend, slug: str, out_dir: Path, *, include_audio: bool) -> dict:
    if not _reciter_exists(backend, slug):
        raise SystemExit(f"slug {slug!r}: no detailed.json under reciters/")
    base = f"reciters/{slug}"

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Top-level JSON/JSONL files (best-effort — optional ones may be missing).
    top_level = [
        "detailed.json",
        "segments.json",
        "edit_history.jsonl",
        "edit_history_peaks.jsonl",
        "low_confidence_v2.json",
        "auto_split_v1.json",
        "pipeline_meta.json",
    ]
    n_top = 0
    for fname in top_level:
        if _download_file(backend, f"{base}/{fname}", out_dir / fname):
            n_top += 1
    log.info("Pulled %d/%d top-level files", n_top, len(top_level))

    # 2. Peaks files (always — they're per-chapter so small, and a
    # migration almost always re-encodes them). ``list_dir`` returns
    # basenames; we need to construct the full bucket path for reads.
    peaks_basenames = _safe_list(backend, f"{base}/peaks/")
    n_peaks = 0
    for entry in peaks_basenames:
        fname = Path(entry).name
        src = f"{base}/peaks/{fname}"
        if _download_file(backend, src, out_dir / "peaks" / fname):
            n_peaks += 1
    log.info("Pulled %d peaks files", n_peaks)

    # 3. Audio sentinel always; audio mp3s only when requested.
    _download_file(backend, f"{base}/audio/_done.json", out_dir / "audio" / "_done.json")

    n_audio = 0
    if include_audio:
        audio_basenames = _safe_list(backend, f"{base}/audio/")
        for entry in audio_basenames:
            fname = Path(entry).name
            if fname == "_done.json":
                continue
            src = f"{base}/audio/{fname}"
            if _download_file(backend, src, out_dir / "audio" / fname):
                n_audio += 1
        log.info("Pulled %d audio files", n_audio)

    return {
        "slug": slug,
        "out_dir": str(out_dir),
        "top_level_files": n_top,
        "peaks_files": n_peaks,
        "audio_files": n_audio,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument("--out", type=Path, required=True, help="Local output directory (slug folder).")
    ap.add_argument(
        "--include-audio",
        action="store_true",
        help="Also download audio/*.mp3 files (default: skip).",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _setup_paths_and_env(args.bucket)

    from services.storage.hf_bucket import get_backend

    backend = get_backend()
    result = download(backend, args.slug, args.out, include_audio=args.include_audio)
    log.info("DONE: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
