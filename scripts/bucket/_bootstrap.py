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
  rl(fn, *a, **kw)                  — run a bucket op with HF-429 backoff
  batch_write(bucket_id, files)     — upload many files (bytes or Paths) in ONE Xet batch
  add_notify_args(parser)           — adds --inspector-url for the ts-refreshed callback
  notify_ts_refreshed(args, slug, …) — fire the post-upload TS-refresh callback (best-effort)
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


def rl(fn, *args, **kwargs):
    """Run a bucket op, backing off on HF 429 rate-limit errors; any other error
    re-raises immediately. Shared so every script retries identically."""
    import re
    import time

    for attempt in range(8):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            if "429" not in msg and "rate limit" not in msg.lower():
                raise
            m = re.search(r"[Rr]etry after (\d+)", msg)
            wait = (int(m.group(1)) + 5) if m else 60
            print(
                f"  rate-limited — sleeping {wait}s (attempt {attempt + 1}/8)",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError("rate-limit retries exhausted")


def batch_write(bucket_id: str, files: dict[str, bytes | Path]) -> None:
    """Upload many bucket files in ONE Xet batch — the fast bulk-write path.

    ``files`` maps a bucket-RELATIVE destination path (e.g.
    ``reciters/<slug>/timestamps/3.json.br``) to its bytes, or to a ``Path``
    that already holds those bytes. A ``bytes`` value is staged to a temp FILE
    and passed to ``batch_bucket_files`` by PATH: passing raw bytes that
    OVERWRITE existing paths hits a ~25x slower server path, and a per-file
    ``fs.open()`` write is one commit per file. A ``Path`` value goes straight
    into the add list, so a caller publishing gigabytes of chapter audio never
    materialises it in memory. Retries on HF 429; no-op when ``files`` is
    empty."""
    if not files:
        return
    import tempfile

    from huggingface_hub import batch_bucket_files

    with tempfile.TemporaryDirectory() as td:
        adds = []
        for i, (dest, body) in enumerate(files.items()):
            if isinstance(body, Path):
                local = body
            else:
                local = Path(td) / f"f{i}"
                local.write_bytes(body)
            adds.append((str(local), dest.lstrip("/")))
        rl(batch_bucket_files, bucket_id, add=adds)


def add_notify_args(parser: argparse.ArgumentParser) -> None:
    """Add ``--inspector-url`` so a backfill can fire the ts-refreshed callback.

    Absent flag (and no ``INSPECTOR_URL`` env) → the callback is skipped and the
    refresh stays silent, exactly as before. The shared secret is read from
    ``INSPECTOR_WEBHOOK_SECRET`` env, never a flag.
    """
    parser.add_argument(
        "--inspector-url",
        default=os.environ.get("INSPECTOR_URL"),
        help="Inspector root URL to POST the ts-refreshed callback to after a "
        "successful write (e.g. https://qud-technologies-quranic-universal-audio.hf.space); "
        "needs INSPECTOR_WEBHOOK_SECRET in env. Omitted = silent update.",
    )


def notify_ts_refreshed(
    args: argparse.Namespace,
    slug: str,
    *,
    chapters: list[int] | None = None,
    reason: str = "manual",
) -> None:
    """Fire the post-upload TS-refresh callback for ``slug`` (best-effort).

    No-op unless ``args`` carries an ``--inspector-url`` (and a secret is in
    env). Routes through the shared ``qua_shared.inspector_notify`` helper so the
    callback contract has one home. Never raises — a failed callback must not
    fail a backfill that already wrote the bucket."""
    url = getattr(args, "inspector_url", None)
    if not url:
        return
    from qua_shared.inspector_notify import notify_ts_refreshed as _notify

    _notify(url, slug, chapters=chapters, reason=reason)
