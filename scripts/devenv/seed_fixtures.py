#!/usr/bin/env python3
"""Tier-0 onboarding: download the public fixtures into a local folder so the
Inspector runs with **no Hugging Face token and no bucket**.

The fixtures live in a *public dataset repo* (anonymously downloadable over the
Hub CDN — unlike buckets, which authenticate every read). This script pulls
them into a local directory and points the app's ``filesystem`` storage
backend at it. After running, ``python inspector/app.py`` boots fully offline.

Usage::

    python scripts/devenv/seed_fixtures.py            # download + configure
    python scripts/devenv/seed_fixtures.py --force    # re-download (reset)
    python scripts/devenv/seed_fixtures.py --print-env # just show the env

What it does:

1. ``snapshot_download`` the public fixtures dataset into ``inspector/.fixtures``
   (gitignored) — laid out exactly like a bucket (``db/inspector.db``,
   ``reciters/<slug>/...``).
2. Configure the app to use it by writing two lines to the repo-root ``.env``:
   ``INSPECTOR_BACKEND=filesystem`` and ``INSPECTOR_FILESYSTEM_ROOT=...``. If a
   ``.env`` already exists it is **never modified** — the lines are printed for
   you to add (so a maintainer's bucket-configured ``.env`` is left untouched).

No token required. The fixtures contain a couple of sample reciters and no
audio/peaks — audio still plays because the proxy streams from the public CDN.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252; force UTF-8 so output never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Public dataset holding the minimal seed (db + sample reciters, JSON only).
FIXTURES_DATASET = os.environ.get(
    "INSPECTOR_FIXTURES_DATASET", "QUD-Technologies/quranic-inspector-fixtures"
)
# Gitignored local root the filesystem backend reads from.
FIXTURES_ROOT = _REPO_ROOT / "inspector" / ".fixtures"

_ENV_LINES = (
    "INSPECTOR_BACKEND=filesystem",
    f"INSPECTOR_FILESYSTEM_ROOT={FIXTURES_ROOT}",
)


def _print_env() -> None:
    print("# Add these to your .env (repo root) or export them:")
    for line in _ENV_LINES:
        print(line)


def _download(force: bool) -> Path:
    from huggingface_hub import snapshot_download

    FIXTURES_ROOT.mkdir(parents=True, exist_ok=True)
    has_db = (FIXTURES_ROOT / "db" / "inspector.db").exists()
    if has_db and not force:
        print(f"==> Fixtures already present at {FIXTURES_ROOT} (use --force to refresh)")
        return FIXTURES_ROOT

    print(f"==> Downloading public fixtures {FIXTURES_DATASET} -> {FIXTURES_ROOT}")
    # Public dataset → no token needed. ``local_dir`` copies real files (not
    # symlinks) so the filesystem backend can read AND write them.
    snapshot_download(
        repo_id=FIXTURES_DATASET,
        repo_type="dataset",
        local_dir=str(FIXTURES_ROOT),
    )
    return FIXTURES_ROOT


def _configure_env() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# Auto-written by scripts/devenv/seed_fixtures.py (Tier-0 offline mode).\n"
            + "\n".join(_ENV_LINES)
            + "\n",
            encoding="utf-8",
        )
        print(f"==> Wrote {env_path} (filesystem backend → fixtures)")
        return

    existing = env_path.read_text(encoding="utf-8")
    if "INSPECTOR_FILESYSTEM_ROOT" in existing:
        print(f"==> {env_path} already configures a filesystem root — leaving it untouched.")
        return
    print(
        f"==> {env_path} already exists; not modifying it. To use fixtures, "
        "add (or set as env vars):"
    )
    _print_env()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if fixtures already exist (resets local edits).",
    )
    p.add_argument(
        "--print-env", action="store_true", help="Only print the env lines; download nothing."
    )
    args = p.parse_args(argv)

    if args.print_env:
        _print_env()
        return 0

    try:
        _download(args.force)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: failed to download fixtures: {e}", file=sys.stderr)
        print(
            "If the dataset doesn't exist yet, a maintainer needs to publish it "
            "with scripts/devenv/make_fixtures_dataset.py.",
            file=sys.stderr,
        )
        return 1

    _configure_env()
    print(
        "\n==> Done. Start the app offline:\n"
        "    cd inspector/frontend && npm install && npm run build\n"
        "    python inspector/app.py            # -> http://localhost:5000\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
