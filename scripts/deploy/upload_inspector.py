#!/usr/bin/env python3
"""Upload the Inspector to its target Hugging Face Space.

Usage::
https://huggingface.co/hetchyy

    python scripts/deploy/upload_inspector.py dev   # → hetchyy/quranic-inspector-dev
    python scripts/deploy/upload_inspector.py prod  # → hetchyy/quranic-universal-audio

Build steps:

1. Stage the Space build context: every git-tracked file, HARDLINKED into a
   temp tree (metadata-only — no byte copy; falls back to a copy only across
   volumes), plus three Space-specific overlays (root ``Dockerfile``, frontmatter
   ``README.md``, and a version-verified local copy of the private renderer
   package). The image's contents are defined solely by
   ``inspector/Dockerfile`` + ``.dockerignore`` (both tracked, both consumed
   identically here and by ``docker-publish.yml``'s repo-root build), so there
   is no hand-maintained file allowlist to drift from the Dockerfile's ``COPY``
   set — ``.dockerignore`` prunes at build time. The frontend ``dist/`` is
   built inside the image (Dockerfile stage 1); nothing is pre-built here.
2. ``upload_folder`` to the Space repo (Hugging Face git+xet under the hood),
   with ``delete_patterns="*"`` so the Space tree mirrors the staged tree.

The renderer overlay rewrites only the staged npm manifest and lockfile to a
``file:vendor/quran-cells`` dependency. Source manifests retain the documented
git-tag dependency, while the unauthenticated Space builder receives the exact
installed tag without a GitHub credential or a second renderer implementation.

Auth: reads ``HF_TOKEN`` (or ``INSPECTOR_HF_TOKEN``) from ``$REPO/.env`` via
``qua_shared._env.load_repo_env``.

Idempotent: re-running uploads only the changed files (the Hub diffs by
content hash). Safe to run from a fresh checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Files NOT pushed to the Space repo: the uncompressed qpc_hafs.json is >10 MB
# and would be auto-LFS-promoted, leaving a 133-byte pointer in the Space build
# context that defeats the Dockerfile COPY. The committed ``.gz`` (~1.5 MB) is
# what the image and the HF Jobs consume; the uncompressed copy stays in the
# repo for local-dev tools that read it directly.
_SKIP_FROM_STAGE = ("data/qpc_hafs.json",)
_CELLS_PACKAGE = "@quranic-phonemizer/cells"
_CELLS_VENDOR_PATH = "vendor/quran-cells"

# Git-LFS pointer files start with this magic line. If an asset got auto-LFS'd
# (by extension or size) it ships as a ~133-byte pointer instead of real bytes,
# silently breaking the image's Dockerfile COPY at runtime. The deploy guard
# fails loudly when any staged file is a pointer rather than its content.
_LFS_POINTER_MAGIC = b"version https://git-lfs.github.com/spec/"

from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError

# HF rate limit is a fixed window (q=2500; w=300s) shared per-token across CI,
# local dev, and bucket reads, so any concurrent burst can 429 the first call
# of a deploy. These bound a polite Retry-After honoring backoff.
_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_DEFAULT_WAIT = 30  # seconds, when no Retry-After header is sent

# Bring in the .env loader so HF_TOKEN is available.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from qua_shared._env import load_repo_env, repo_root  # noqa: E402

SPACE_REPOS = {
    "dev": "hetchyy/quranic-inspector-dev",
    "prod": "hetchyy/quranic-universal-audio",
}

# README frontmatter shipped to the Space. Mirrors the runbook §1 setup.
#
# Deliberately no `hf_oauth`. It makes HF provision its own OAuth app and inject
# OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET, which collides with the Space secrets of
# the same name that hold our own registered app — HF rejects the whole config
# ("Collision on variables and secrets names") and the Space never boots. Our own
# app is required anyway: HF's auto-provisioned one allow-lists only the Space's
# own hosts, so a custom domain's redirect_uri is refused with a 400.
#
# `header: mini` is deliberately absent. It makes the huggingface.co Space page
# float HF's pill over a full-viewport iframe, in the same top-right corner the
# app keeps its own controls — and the pill lives in that cross-origin page, so
# the app can neither measure nor avoid it. `@huggingface/space-header` cannot
# stand in as a ruler either: it returns early when `ancestorOrigins` contains
# huggingface.co, which is exactly the embedded case. Without the key the Space
# page draws its normal header above the app and nothing overlaps; the custom
# domain is unaffected either way and gets its pill from the package
# (`frontend/src/lib/utils/space-header.ts`).
SPACE_README = """---
title: Quranic Universal Audio{suffix}
emoji: 🎙️
colorFrom: yellow
colorTo: indigo
short_description: Visualize, edit & verify Qur'anic recitation timestamps
sdk: docker
app_port: 7860
pinned: false
---
"""


def _git_tracked_files(repo: Path) -> list[str]:
    """Repo-relative paths of every git-tracked file (the Space build context)."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [p for p in out.stdout.split("\0") if p]


def _assert_no_lfs_pointers(stage_root: Path) -> None:
    """Abort the deploy if any staged file is a Git-LFS pointer, not its bytes.

    An asset that got auto-LFS-promoted (by extension or size) is staged as a
    ~133-byte pointer text file. Shipping that to the Space leaves the Dockerfile
    ``COPY`` pointing at a stub instead of real content — a silent runtime break
    that CI's image build doesn't catch. Fail loudly here so the operator sees it
    before the upload, not after the Space starts serving garbage.
    """
    pointers: list[str] = []
    n = len(_LFS_POINTER_MAGIC)
    for path in stage_root.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as fh:
            head = fh.read(n)
        if head == _LFS_POINTER_MAGIC:
            pointers.append(str(path.relative_to(stage_root)))
    if pointers:
        listing = "\n  ".join(sorted(pointers))
        raise RuntimeError(
            "Refusing to deploy: the following staged files are Git-LFS "
            "pointers, not their real bytes (they were auto-LFS-promoted and "
            "would ship to the Space as ~133-byte stubs, breaking the "
            "Dockerfile COPY at runtime):\n  "
            f"{listing}\n"
            "Fix by un-LFS'ing the asset, shipping a non-LFS variant (e.g. a "
            "compressed .gz), or inlining it — see _SKIP_FROM_STAGE above."
        )


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` → ``dst`` (metadata-only, near-instant); fall back to a
    byte copy only on EXDEV / a filesystem that can't hardlink.

    The staged tree is READ-ONLY input to ``upload_folder`` + the LFS-pointer
    guard, so sharing the worktree's inode is safe and skips the O(all-files)
    byte copy that dominated deploy time on Windows. The temp dir is on the same
    volume as the repo (both on the system drive), so the hardlink path is taken;
    removing the temp tree just drops the extra link, never the worktree file.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _stage_cells_package(repo: Path, stage_root: Path) -> None:
    """Materialize the private tagged renderer package in the Space context.

    Source checkouts retain the documented git-tag dependency. Space builders
    cannot authenticate to that private GitHub repository, so the deploy
    artifact uses the exact locally installed package as a temporary ``file:``
    dependency. The installed version must match the tag in ``package.json``.
    """
    frontend = repo / "inspector" / "frontend"
    source = frontend / "node_modules" / "@quranic-phonemizer" / "cells"
    source_manifest = source / "package.json"
    if not source_manifest.is_file():
        raise RuntimeError(
            f"{_CELLS_PACKAGE} is not installed; run npm ci in {frontend} before deploying"
        )
    package_meta = json.loads(source_manifest.read_text(encoding="utf-8"))
    version = str(package_meta.get("version", ""))

    staged_frontend = stage_root / "inspector" / "frontend"
    manifest_path = staged_frontend / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = str(manifest.get("dependencies", {}).get(_CELLS_PACKAGE, ""))
    if not version or f"cells-v{version}" not in declared:
        raise RuntimeError(
            f"installed {_CELLS_PACKAGE} {version or '<unknown>'} does not match {declared!r}"
        )

    vendor = staged_frontend / _CELLS_VENDOR_PATH
    shutil.copytree(source, vendor)
    manifest["dependencies"][_CELLS_PACKAGE] = f"file:{_CELLS_VENDOR_PATH}"
    manifest_path.unlink()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    lock_path = staged_frontend / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock["packages"]
    packages[""]["dependencies"][_CELLS_PACKAGE] = f"file:{_CELLS_VENDOR_PATH}"
    packages[f"node_modules/{_CELLS_PACKAGE}"] = {
        "resolved": _CELLS_VENDOR_PATH,
        "link": True,
    }
    packages[_CELLS_VENDOR_PATH] = {
        key: package_meta[key]
        for key in ("name", "version", "peerDependencies")
        if key in package_meta
    }
    lock_path.unlink()
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


def _stage(repo: Path, stage_root: Path, env: str, branch: str) -> None:
    """Stage the Space build context: every git-tracked file, hardlinked.

    The image's contents are defined solely by ``inspector/Dockerfile`` +
    ``.dockerignore``, so there is no hand-curated allowlist to drift from the
    Dockerfile's ``COPY`` set — ``.dockerignore`` prunes at build time. Three
    Space-specific overlays are written on top of the
    tracked tree:

    * ``Dockerfile`` at the root — the Hub docker SDK reads it there, but ours
      lives under ``inspector/``.
    * ``README.md`` — the Space frontmatter, written last so it wins over the
      repo's tracked root ``README.md``.
    * ``inspector/frontend/vendor/quran-cells`` plus staged npm manifests — the
      exact installed private renderer tag, available without build credentials.

    The frontend ``dist/`` is built inside the image (Dockerfile stage 1), so
    nothing is pre-built here.
    """
    skip = set(_SKIP_FROM_STAGE)
    for rel in _git_tracked_files(repo):
        if rel in skip:
            continue
        src = repo / rel
        if not src.is_file():
            continue  # tracked but absent in the worktree (e.g. deleted)
        dst = stage_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(src, dst)

    _stage_cells_package(repo, stage_root)

    _link_or_copy(repo / "inspector" / "Dockerfile", stage_root / "Dockerfile")

    suffix = " (dev)" if env == "dev" else ""
    readme_path = stage_root / "README.md"
    readme_path.unlink(missing_ok=True)
    readme_path.write_text(
        SPACE_README.format(suffix=suffix, branch=branch),
        encoding="utf-8",
    )

    _assert_no_lfs_pointers(stage_root)


def _whole_private_key(value: str) -> str:
    """A private key normalised for OpenSSH, or a loud failure.

    A secret that lost its body — the usual cause is a CI input that split the
    multi-line value on newlines and kept only the first — is still present and
    non-empty, so the Space builds, the qua-domain fetch fails as "Permission
    denied (publickey)", and the image quietly ships Hafs-only. That surfaces
    days later as a Warsh delivery that will not render, so check it here while
    the cause is still visible.

    CRLF and a missing trailing newline are the other two ways a round-trip
    breaks a key, and both make OpenSSH refuse it outright ("error in libcrypto:
    unsupported"). Repairing them costs nothing; the Dockerfile does the same to
    its own copy, because the Space's build reads the secret directly.
    """
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    head, _, body = text.partition("\n")
    if not (head.startswith("-----BEGIN ") and head.endswith("-----")):
        raise SystemExit("QUA_DOMAIN_DEPLOY_KEY does not begin with a PEM header")
    if not body.strip():
        raise SystemExit(
            "QUA_DOMAIN_DEPLOY_KEY is a header with no body — the secret was "
            "truncated in transit; check that the workflow quotes it"
        )
    if not text.endswith("-----"):
        raise SystemExit("QUA_DOMAIN_DEPLOY_KEY does not end with a PEM footer")
    return text + "\n"


def _retry_on_429(label: str, fn, *args, **kwargs):
    """Call ``fn``, retrying on HTTP 429 with a Retry-After honoring backoff.

    The Hub rate limit is a per-token fixed window shared across every process
    using the token (CI, local dev, bucket reads), so a transient 429 here is
    expected and retryable — not a fatal deploy error.
    """
    for attempt in range(1, _RATE_LIMIT_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except HfHubHTTPError as exc:
            resp = exc.response
            is_429 = resp is not None and resp.status_code == 429
            if not is_429 or attempt == _RATE_LIMIT_RETRIES:
                raise
            wait = int(resp.headers.get("Retry-After", _RATE_LIMIT_DEFAULT_WAIT))
            print(
                f"==> 429 rate-limited on {label}; sleeping {wait}s "
                f"(attempt {attempt}/{_RATE_LIMIT_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(wait)


def _upload(
    stage_root: Path,
    repo_id: str,
    token: str,
    commit_msg: str,
    cells_deploy_key: str,
    qua_domain_deploy_key: str,
) -> str:
    api = HfApi(token=token)
    # The prod/dev Spaces are permanent — skip the create_repo call (it 409s on
    # every deploy and still spends a request) unless the Space is actually
    # missing, e.g. a first-time contributor bootstrap.
    if not _retry_on_429("repo_exists", api.repo_exists, repo_id=repo_id, repo_type="space"):
        _retry_on_429(
            "create_repo",
            api.create_repo,
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=True,
            exist_ok=True,
        )
    _retry_on_429(
        "add_space_secret",
        api.add_space_secret,
        repo_id=repo_id,
        key="CELLS_DEPLOY_KEY",
        value=cells_deploy_key,
    )
    # Unlike CELLS_DEPLOY_KEY (hard-required — the frontend does not build
    # without the renderer package), qua-domain is optional: without it the
    # Space builds and serves Hafs, and every non-Hafs delivery fails loudly.
    # Pushing an empty value would overwrite a good secret already on the Space.
    if qua_domain_deploy_key:
        _retry_on_429(
            "add_space_secret",
            api.add_space_secret,
            repo_id=repo_id,
            key="QUA_DOMAIN_DEPLOY_KEY",
            value=_whole_private_key(qua_domain_deploy_key),
        )
    else:
        print(
            "    WARN: QUA_DOMAIN_DEPLOY_KEY absent from env; leaving the Space "
            "secret as-is. A Space that never had it builds Hafs-only."
        )
    _retry_on_429(
        "upload_folder",
        api.upload_folder,
        folder_path=str(stage_root),
        repo_id=repo_id,
        repo_type="space",
        commit_message=commit_msg,
        delete_patterns="*",
    )
    # Code-only pushes don't change the Dockerfile hash, so HF doesn't
    # rebuild the container automatically — the new commit shows up in
    # the Space repo but the running container still serves the previous
    # bundle. Force a factory reboot so the new code actually runs.
    _retry_on_429("restart_space", api.restart_space, repo_id=repo_id, factory_reboot=True)
    return f"https://huggingface.co/spaces/{repo_id}"


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("env", choices=("dev", "prod"))
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Stage but don't upload. Prints stage path and exits.",
    )
    p.add_argument(
        "--verify-boot",
        action="store_true",
        help=(
            "Before uploading, build the staged context and boot it offline "
            "(filesystem fixtures), asserting /healthz is 200. Aborts the "
            "deploy if the image won't build or boot. Needs Docker."
        ),
    )
    args = p.parse_args(argv)

    load_repo_env()
    token = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        print(
            "ERROR: HF_TOKEN / INSPECTOR_HF_TOKEN missing in env. "
            "Add it to .env at the worktree root.",
            file=sys.stderr,
        )
        return 2
    cells_deploy_key = os.environ.get("CELLS_DEPLOY_KEY")
    if not cells_deploy_key and not args.dry_run:
        print("ERROR: CELLS_DEPLOY_KEY missing in env.", file=sys.stderr)
        return 2

    repo = repo_root()
    repo_id = SPACE_REPOS[args.env]
    branch = "dev" if args.env == "dev" else "main"

    print(f"==> Deploying Inspector to {args.env} ({repo_id})")
    sha = _commit_sha(repo)
    commit_msg = f"deploy: inspector {args.env} @ {sha}"

    with tempfile.TemporaryDirectory(prefix=f"inspector-stage-{args.env}-") as tmp:
        stage_root = Path(tmp)
        print(f"==> Staging Space tree in {stage_root}")
        _stage(repo, stage_root, args.env, branch)

        if args.verify_boot:
            print("==> Verifying the staged image builds and boots...")
            smoke = repo / "scripts" / "deploy" / "smoke_boot.py"
            subprocess.run(
                [sys.executable, str(smoke), "--context", str(stage_root)],
                check=True,
            )

        if args.dry_run:
            print(f"==> Dry run; stage at {stage_root} (not deleted on dry-run)")
            # Move out of the temp context so it survives.
            keep = repo / ".local" / "inspector-stage" / args.env
            keep.parent.mkdir(parents=True, exist_ok=True)
            if keep.exists():
                shutil.rmtree(keep)
            shutil.copytree(stage_root, keep)
            print(f"    Copied to {keep}")
            return 0

        print(f"==> Uploading to {repo_id}")
        url = _upload(
            stage_root,
            repo_id,
            token,
            commit_msg,
            cells_deploy_key or "",
            os.environ.get("QUA_DOMAIN_DEPLOY_KEY") or "",
        )
        print(f"==> Done. Space: {url}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
