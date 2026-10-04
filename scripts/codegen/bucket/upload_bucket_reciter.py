"""Upload a local reciter folder back to a bucket (post-migration).

Companion to ``download_bucket_reciter.py``. Designed for the post-
download → migrate → audit → reupload workflow on legacy reciters.

Uploads every top-level JSON/JSONL artefact present in the local folder,
every ``peaks/<ch>.json.gz``, every ``timestamps/<ch>.json.br``, and (optionally) every
``audio/<ch>.mp3``. Deletes any ``peaks/<ch>.json`` orphans left on the
bucket from a pre-Migration #5 layout (their slim ``.json.gz``
replacements were created locally during the migration).

Pre-flight:
    * ``--apply`` is required to write. Without it, just prints the plan.
    * Audit the local folder first via ``audit_bucket_reciter.py --local-path``
      — this script trusts the local bytes are canonical.

Usage::

    python3 scripts/bucket/upload_bucket_reciter.py \\
        --slug mahmoud_khalil_al_husary_mp3quran \\
        --src /tmp/mahmoud_khalil_al_husary_mp3quran/ \\
        --bucket prod --apply
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("upload_bucket_reciter")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

_TOP_LEVEL_FILES = [
    "detailed.json",
    "segments.json",
    "edit_history.jsonl",
    "edit_history_peaks.jsonl",
    "low_confidence_v2.json",
    "auto_split_v1.json",
    "pipeline_meta.json",
]


def _setup_paths_and_env(bucket: str) -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    sys.path.insert(0, str(repo / "inspector"))
    sys.path.insert(0, str(repo))
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


def upload(backend, slug: str, src: Path, *, include_audio: bool, apply: bool) -> dict:
    base = f"reciters/{slug}"
    log.info("Target: %s/", base)

    # 1. Plan: top-level files present locally → upload list.
    to_upload: list[tuple[Path, str]] = []
    for fname in _TOP_LEVEL_FILES:
        p = src / fname
        if p.is_file():
            to_upload.append((p, f"{base}/{fname}"))

    # 2. Plan: every peaks/<ch>.json.gz locally → upload.
    peaks_local_dir = src / "peaks"
    peaks_local = (
        sorted(p for p in peaks_local_dir.iterdir() if p.is_file() and p.name.endswith(".json.gz"))
        if peaks_local_dir.is_dir()
        else []
    )
    for p in peaks_local:
        to_upload.append((p, f"{base}/peaks/{p.name}"))

    timestamps_dir = src / "timestamps"
    timestamps = (
        sorted(p for p in timestamps_dir.iterdir() if p.is_file() and p.name.endswith(".json.br"))
        if timestamps_dir.is_dir()
        else []
    )
    for p in timestamps:
        to_upload.append((p, f"{base}/timestamps/{p.name}"))

    # 3. Plan: audio sentinel + (optionally) mp3s.
    sentinel = src / "audio" / "_done.json"
    if sentinel.is_file():
        to_upload.append((sentinel, f"{base}/audio/_done.json"))
    n_audio = 0
    if include_audio:
        audio_dir = src / "audio"
        if audio_dir.is_dir():
            for p in sorted(audio_dir.iterdir()):
                if p.is_file() and p.name.endswith(".mp3"):
                    to_upload.append((p, f"{base}/audio/{p.name}"))
                    n_audio += 1

    # 4. Plan: orphan deletes — any peaks/<ch>.json on the bucket that
    # has a local .json.gz counterpart (or doesn't, but is plain legacy).
    orphans: list[str] = []
    try:
        bucket_peaks = backend.list_dir(f"{base}/peaks/") or []
    except Exception:
        bucket_peaks = []
    for entry in bucket_peaks:
        name = Path(entry).name
        if name.endswith(".json") and not name.endswith(".json.gz"):
            orphans.append(f"{base}/peaks/{name}")

    log.info(
        "Plan: upload %d top-level + %d peaks .gz + %d timestamps .br + %d audio (sentinel%s); "
        "delete %d orphan plain peaks",
        sum(1 for _, dst in to_upload if "/peaks/" not in dst and "/audio/" not in dst),
        sum(1 for _, dst in to_upload if "/peaks/" in dst),
        sum(1 for _, dst in to_upload if "/timestamps/" in dst),
        n_audio,
        " + audio mp3s" if include_audio else "",
        len(orphans),
    )

    if not apply:
        log.warning("DRY RUN — pass --apply to write. Nothing pushed.")
        return {
            "slug": slug,
            "apply": False,
            "uploads_planned": len(to_upload),
            "orphan_deletes_planned": len(orphans),
        }

    # 5. Write.
    n_up = 0
    for p, dst in to_upload:
        backend.write_bytes_atomic(dst, p.read_bytes())
        n_up += 1
        if n_up % 25 == 0:
            log.info("  uploaded %d/%d", n_up, len(to_upload))
    log.info("Uploaded %d files", n_up)

    n_del = 0
    for rel in orphans:
        try:
            backend.delete(rel)
            n_del += 1
        except Exception as e:
            log.warning("delete failed %s: %s", rel, e)
    log.info("Deleted %d orphan plain peaks files", n_del)

    return {
        "slug": slug,
        "apply": True,
        "uploaded": n_up,
        "deleted_orphans": n_del,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--slug", required=True)
    ap.add_argument("--bucket", choices=sorted(_BUCKETS), default="prod")
    ap.add_argument("--src", type=Path, required=True, help="Local slug folder.")
    ap.add_argument(
        "--include-audio", action="store_true", help="Also upload audio/*.mp3 (default: skip)."
    )
    ap.add_argument(
        "--apply", action="store_true", help="Actually write. Without it, just prints the plan."
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.src.is_dir():
        raise SystemExit(f"not a directory: {args.src}")

    _setup_paths_and_env(args.bucket)
    from services.storage.hf_bucket import get_backend

    backend = get_backend()

    result = upload(
        backend, args.slug, args.src, include_audio=args.include_audio, apply=args.apply
    )
    log.info("DONE: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
