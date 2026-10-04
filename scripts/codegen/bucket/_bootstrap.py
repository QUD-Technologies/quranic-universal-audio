"""Shared helpers for the hf-buckets utility scripts.

Why this exists: every script needs the same five things — find the repo
root, load ``HF_TOKEN`` from ``.env``, pick the right bucket id from a
``--bucket`` flag, construct an ``HfFileSystem``, and not silently drop
through to prod when the caller forgot a flag. Centralised so a new
script is ~20 lines.

Bucket ids (mirror ``inspector/services/storage/hf_bucket.py``):
  dev      → QUD-Technologies/quranic-inspector-bucket-dev   (default everywhere off-Space)
  prod     → QUD-Technologies/quranic-inspector-bucket        (live; mutating ops need --yes-prod)
  aligner  → hetchyy/aligner-bucket                  (private; MFA stack + staged code)

Public surface used by sibling scripts:
  add_bucket_args(parser)           — adds --bucket and --yes-prod flags
  resolve(args) → (fs, bucket_id)   — loads token, returns ready-to-use HfFileSystem
  fmt_size(n)                       — "1.2 MB" / "823 KB" / "3.1 GB"
  confirm_mutation(args, action)    — bails unless --yes-prod set on --bucket prod
  abs_path(bucket_id, sub)          — hf://buckets/<id>/<sub> formatter
  ensure_utf8_stdout()              — Windows cp1252 → utf-8 patch
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Match resolve_bucket_repo in inspector/services/storage/hf_bucket.py.
BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
    "aligner": "hetchyy/aligner-bucket",
}


def repo_root() -> Path:
    # scripts/bucket/_bootstrap.py → bucket/ → scripts/ → repo
    return Path(__file__).resolve().parents[2]


def ensure_utf8_stdout() -> None:
    """Reconfigure stdout/stderr to UTF-8 so emoji + Arabic in slugs survive
    on Windows cp1252 terminals. No-op elsewhere."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def load_hf_token() -> str | None:
    """``HF_TOKEN`` env wins; otherwise read from ``.env`` at repo root."""
    tok = os.environ.get("HF_TOKEN", "").strip()
    if tok:
        return tok
    env_file = repo_root() / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("HF_TOKEN="):
                tok = line.split("=", 1)[1].strip()
                if tok:
                    os.environ["HF_TOKEN"] = tok
                    return tok
    return None


def add_bucket_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bucket", choices=list(BUCKETS), default="dev", help="which bucket (default: dev)"
    )
    parser.add_argument(
        "--yes-prod", action="store_true", help="confirm a mutating op against --bucket prod"
    )


def resolve(args: argparse.Namespace):
    """Return ``(HfFileSystem, bucket_id)``. Imports HfFileSystem lazily so
    --help is fast and tools without huggingface_hub installed still print."""
    ensure_utf8_stdout()
    if not load_hf_token():
        print("warning: HF_TOKEN not in env or .env — anonymous calls may 401", file=sys.stderr)
    from huggingface_hub import HfFileSystem

    return HfFileSystem(), BUCKETS[args.bucket]


def abs_path(bucket_id: str, sub: str = "") -> str:
    sub = sub.lstrip("/")
    return f"hf://buckets/{bucket_id}/{sub}" if sub else f"hf://buckets/{bucket_id}"


def fmt_size(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def confirm_mutation(args: argparse.Namespace, action: str) -> None:
    """Abort unless --yes-prod is set on a --bucket prod mutating op.

    Mirrors the ``INSPECTOR_ALLOW_PROD_BUCKET=1`` gate inspector/services/
    storage/hf_bucket.py applies to its own writes — same intent, surfaced
    inline because these scripts skip that import path."""
    if args.bucket != "prod":
        return
    if not args.yes_prod:
        print(f"refusing to {action} on prod bucket without --yes-prod", file=sys.stderr)
        sys.exit(2)
