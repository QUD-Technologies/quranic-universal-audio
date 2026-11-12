#!/usr/bin/env python3
"""Deploy the Inspector to *any* Hugging Face Space — including your own.

This is the committed, parameterized cousin of the maintainer-only
``scripts/deploy/upload_inspector.py`` (which is hardcoded to the canonical dev/prod
Spaces). Contributors use this to push to a personal Space created by
``bootstrap_dev_env.py``.

Usage::

    # Deploy to your personal Space (created by bootstrap_dev_env.py)
    python scripts/deploy/deploy_space.py <your-hf-user>/quranic-inspector-<name>

    # Stage only, don't upload (inspect the tree that would ship)
    python scripts/deploy/deploy_space.py <id> --dry-run

What it does:

1. Builds the frontend (``inspector/frontend`` → ``dist/``) unless
   ``--skip-build``.
2. Stages the Space-shaped tree (Dockerfile, ``inspector/`` code + built
   ``dist/``, ``qua_shared/``, the
   talqeen-quran-data installer the image build runs, a ``README.md`` with the HF
   Space frontmatter).
3. ``create_repo`` (idempotent) → ``upload_folder`` → factory ``restart_space``.

The README frontmatter sets ``hf_oauth: true`` so Hugging Face auto-injects
``OAUTH_CLIENT_ID`` / ``OAUTH_CLIENT_SECRET`` — you never register an OAuth
app yourself. The only secrets you set on the Space are your own ``HF_TOKEN``
(bucket access) and an auto-generated ``INSPECTOR_SESSION_SECRET``; both are
handled by ``bootstrap_dev_env.py``. The image build also needs
``TALQEEN_DATA_READ_TOKEN`` (read access to ``QUD-Technologies/talqeen-ai-data``) for
the Quran data; when it is in the environment this script sets it as a Space secret.

Auth: reads ``HF_TOKEN`` (or ``INSPECTOR_HF_TOKEN``) from the environment or
the repo-root ``.env``.

Idempotent: re-runs upload only changed files (the Hub diffs by content hash).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252; force UTF-8 so output never crashes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# Put the repo root on sys.path so ``qua_shared._env`` resolves whether this
# is launched as a module or a file.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_shared._env import load_repo_env, repo_root  # noqa: E402

# README frontmatter shipped to the Space. ``hf_oauth: true`` is load-bearing:
# it makes HF inject the OAuth client credentials the app reads at runtime.
SPACE_README = """---
title: {title}
emoji: 🎙️
colorFrom: yellow
colorTo: indigo
short_description: Visualize, edit & verify Qur'anic recitation timestamps
sdk: docker
app_port: 7860
pinned: false
hf_oauth: true
hf_oauth_expiration_minutes: 480
---

Personal dev deployment of the Quranic Universal Audio Inspector.
Built from a contributor branch — not the production site.
"""


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def _build_frontend(repo: Path) -> None:
    fe = repo / "inspector" / "frontend"
    if not (fe / "node_modules").is_dir():
        _run(["npm", "ci"], cwd=fe)
    _run(["npm", "run", "build"], cwd=fe)


def _stage(repo: Path, stage_root: Path, *, title: str) -> None:
    (stage_root / "README.md").write_text(SPACE_README.format(title=title), encoding="utf-8")

    # Dockerfile + .dockerignore sit at the Space root.
    shutil.copy2(repo / "inspector" / "Dockerfile", stage_root / "Dockerfile")
    shutil.copy2(repo / ".dockerignore", stage_root / ".dockerignore")

    # inspector/ code tree (drop test + build caches; keep built dist/).
    shutil.copytree(
        repo / "inspector",
        stage_root / "inspector",
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "tests",
            ".pytest_cache",
            ".mypy_cache",
            "node_modules",
            ".vite",
            ".bucket",
        ),
    )

    # qua_shared/ + qua_jobs/ ship — the runtime imports ``qua_shared.*`` and
    # ``services/admin/jobs`` stages ``qua_jobs/`` to the job bucket on launch.
    # Top-level CLIs under scripts/ are not needed inside the Space.
    for pkg in ("qua_shared", "qua_jobs"):
        shutil.copytree(
            repo / pkg,
            stage_root / pkg,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    # The Quran data pack is fetched by the image build with the installer staged here.
    installer = Path("scripts") / "devenv" / "install_quran_data.py"
    (stage_root / installer).parent.mkdir(parents=True)
    shutil.copy2(repo / installer, stage_root / installer)


def _commit_sha(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _validate_space_id(space_id: str) -> str:
    if space_id.count("/") != 1 or not all(space_id.split("/")):
        raise SystemExit(f"space id must be '<owner>/<name>', got {space_id!r}")
    return space_id


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("space", help="Target Space id, e.g. you/quranic-inspector-foo")
    p.add_argument(
        "--public", action="store_true", help="Create the Space public (default: private)."
    )
    p.add_argument(
        "--skip-build", action="store_true", help="Skip `npm run build`; ship the existing dist/."
    )
    p.add_argument("--no-restart", action="store_true", help="Don't factory-reboot after upload.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Stage but don't create/upload. Prints the stage path.",
    )
    args = p.parse_args(argv)

    space_id = _validate_space_id(args.space)
    load_repo_env()
    token = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if not token and not args.dry_run:
        print(
            "ERROR: HF_TOKEN / INSPECTOR_HF_TOKEN missing. Add it to .env at "
            "the repo root or export it.",
            file=sys.stderr,
        )
        return 2

    repo = repo_root()
    title = f"Inspector ({space_id.split('/')[1]})"

    print(f"==> Building Inspector for Space {space_id}")
    if not args.skip_build:
        _build_frontend(repo)
    else:
        print("  (skip-build) using existing dist/")

    if not (repo / "inspector" / "frontend" / "dist" / "index.html").exists():
        print(
            "ERROR: inspector/frontend/dist/index.html missing — build the "
            "frontend first (drop --skip-build).",
            file=sys.stderr,
        )
        return 2

    sha = _commit_sha(repo)
    commit_msg = f"deploy: inspector @ {sha}"

    import tempfile

    with tempfile.TemporaryDirectory(prefix="inspector-stage-") as tmp:
        stage_root = Path(tmp)
        print(f"==> Staging Space tree in {stage_root}")
        _stage(repo, stage_root, title=title)

        if args.dry_run:
            keep = repo / ".local" / "inspector-stage" / space_id.replace("/", "__")
            keep.parent.mkdir(parents=True, exist_ok=True)
            if keep.exists():
                shutil.rmtree(keep)
            shutil.copytree(stage_root, keep)
            print(f"==> Dry run; staged tree copied to {keep}")
            return 0

        from huggingface_hub import HfApi

        api = HfApi(token=token)
        print(f"==> Ensuring Space {space_id} exists (private={not args.public})")
        api.create_repo(
            repo_id=space_id,
            repo_type="space",
            space_sdk="docker",
            private=not args.public,
            exist_ok=True,
        )
        talqeen_token = os.environ.get("TALQEEN_DATA_READ_TOKEN")
        if talqeen_token:
            api.add_space_secret(
                repo_id=space_id, key="TALQEEN_DATA_READ_TOKEN", value=talqeen_token
            )
        else:
            print("  TALQEEN_DATA_READ_TOKEN unset; the Space must already hold it as a secret")
        print(f"==> Uploading to {space_id}")
        api.upload_folder(
            folder_path=str(stage_root),
            repo_id=space_id,
            repo_type="space",
            commit_message=commit_msg,
            delete_patterns="*",
        )
        if not args.no_restart:
            # Code-only pushes don't change the Dockerfile hash, so HF won't
            # rebuild automatically — force a factory reboot.
            api.restart_space(repo_id=space_id, factory_reboot=True)
        print(f"==> Done. Space: https://huggingface.co/spaces/{space_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
