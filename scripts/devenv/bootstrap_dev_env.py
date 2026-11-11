#!/usr/bin/env python3
"""Tier-1/2 onboarding: provision a personal bucket + Space under *your* HF
account, so you get full backend/DB/bucket behaviour isolated from everyone
else.

Why isolation is mandatory: the Inspector's SQLite DB syncs full-file to its
bucket, so two people sharing one bucket clobber each other's state. Every
contributor gets their own bucket.

What this does (all under your own namespace, using your own token):

1. Create a private bucket ``<you>/quranic-inspector-<name>``.
2. Seed it from the public fixtures dataset (Hub copy; small files round-trip
   through the local cache, large Xet files copy server-side).
3. Create a private docker Space ``<you>/quranic-inspector-<name>``.
4. Attach the bucket as a Space volume at ``/data/inspector-bucket`` (where the
   image expects it) — so there's no manual "Attach bucket" click in the UI.
5. Set the Space's secrets/variables:
     - ``HF_TOKEN``            (secret)   — lets the Space read/write your bucket
     - ``INSPECTOR_SESSION_SECRET`` (secret, auto-generated) — signs cookies
     - ``INSPECTOR_BUCKET_REPO``    (variable) — points the Space at your bucket
   OAuth client credentials are injected automatically by HF because the Space
   README sets ``hf_oauth: true`` — you never register an OAuth app.
6. (optional, --deploy) push the current code to the Space.

End to end, the only thing you do by hand is create an HF token — no clicking
in the HF UI to clone/duplicate Spaces or buckets, attach storage, or enter
secrets.

Usage::

    python scripts/devenv/bootstrap_dev_env.py myname            # provision
    python scripts/devenv/bootstrap_dev_env.py myname --deploy    # + push code
    python scripts/devenv/bootstrap_dev_env.py myname --dry-run   # show plan only
    python scripts/devenv/bootstrap_dev_env.py myname --teardown  # delete both

Auth: reads ``HF_TOKEN`` (or ``INSPECTOR_HF_TOKEN``) from env or repo-root
``.env``. Tip: use a token scoped to your own repos.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode characters like the
# arrow used in help text — force UTF-8 so output never crashes cross-platform.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# Seeding the bucket (copy_files) routes non-Xet files through the local HF
# cache, which uses symlinks. On Windows without Developer Mode/admin that
# raises WinError 1314 ("required privilege is not held"). Disabling symlinks
# makes the cache copy real files — required for the one-command bootstrap to
# work cross-platform.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_shared._env import load_repo_env  # noqa: E402

FIXTURES_DATASET = os.environ.get(
    "INSPECTOR_FIXTURES_DATASET", "QUD-Technologies/quranic-inspector-fixtures"
)


def _resolve_token() -> str:
    load_repo_env()
    token = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit(
            "HF_TOKEN / INSPECTOR_HF_TOKEN missing. Add it to .env at the repo "
            "root or export it. Create one at https://huggingface.co/settings/tokens"
        )
    return token


def _whoami(api) -> str:
    info = api.whoami()
    name = info.get("name") if isinstance(info, dict) else None
    if not name:
        raise SystemExit("Could not resolve your HF username from the token.")
    return name


def _provision(api, *, user: str, name: str, deploy: bool, public_bucket: bool) -> None:
    from huggingface_hub import Volume, copy_files, create_bucket

    bucket_id = f"{user}/quranic-inspector-{name}"
    space_id = f"{user}/quranic-inspector-{name}"

    print(f"==> [1/6] Creating bucket {bucket_id} (private={not public_bucket})")
    create_bucket(f"quranic-inspector-{name}", private=not public_bucket, exist_ok=True)

    print(f"==> [2/6] Seeding bucket from {FIXTURES_DATASET}")
    copy_files(
        f"hf://datasets/{FIXTURES_DATASET}/",
        f"hf://buckets/{bucket_id}/",
    )

    print(f"==> [3/6] Creating Space {space_id} (private)")
    api.create_repo(
        repo_id=space_id,
        repo_type="space",
        space_sdk="docker",
        private=True,
        exist_ok=True,
    )

    # The Docker image expects the bucket NFS-mounted at /data/inspector-bucket
    # (INSPECTOR_BUCKET_MOUNT). Attach it programmatically so there's no manual
    # "Attach bucket" step in the Space UI.
    print("==> [4/6] Attaching bucket volume at /data/inspector-bucket")
    api.set_space_volumes(
        space_id,
        volumes=[Volume(type="bucket", source=bucket_id, mount_path="/data/inspector-bucket")],
    )

    print("==> [5/6] Setting Space secrets + variables")
    session_secret = secrets.token_hex(32)
    token = api.token
    api.add_space_secret(
        space_id, "HF_TOKEN", token, description="Bucket access for this dev Space"
    )
    api.add_space_secret(
        space_id,
        "INSPECTOR_SESSION_SECRET",
        session_secret,
        description="Cookie-signing key (auto-generated)",
    )
    api.add_space_variable(
        space_id, "INSPECTOR_BUCKET_REPO", bucket_id, description="This dev's isolated bucket"
    )

    if deploy:
        print("==> [6/6] Deploying current code to the Space")
        import subprocess

        deploy_script = _REPO_ROOT / "scripts" / "deploy" / "deploy_space.py"
        subprocess.run([sys.executable, str(deploy_script), space_id], check=True)
    else:
        print("==> [6/6] Skipping deploy. Push code when ready with:")
        print(f"        python scripts/deploy/deploy_space.py {space_id}")

    print("\n==> Done.")
    print(f"    Bucket : hf://buckets/{bucket_id}")
    print(f"    Space  : https://huggingface.co/spaces/{space_id}")
    print("\n    For LOCAL Tier-1 dev against this bucket, set in your .env:")
    print(f"        INSPECTOR_BUCKET_REPO={bucket_id}")
    print("        HF_TOKEN=<your token>")


def _teardown(api, *, user: str, name: str) -> None:
    from huggingface_hub import delete_bucket

    bucket_id = f"{user}/quranic-inspector-{name}"
    space_id = f"{user}/quranic-inspector-{name}"
    print(f"==> Deleting Space {space_id}")
    try:
        api.delete_repo(repo_id=space_id, repo_type="space", missing_ok=True)
    except TypeError:
        api.delete_repo(repo_id=space_id, repo_type="space")
    print(f"==> Deleting bucket {bucket_id} (PERMANENT)")
    delete_bucket(bucket_id, missing_ok=True)
    print("==> Teardown complete.")


def _print_plan(*, user: str, name: str, deploy: bool, teardown: bool) -> None:
    bid = f"{user}/quranic-inspector-{name}"
    if teardown:
        print("DRY RUN — would delete:")
        print(f"  - Space  {bid}")
        print(f"  - bucket {bid}")
        return
    print("DRY RUN — would create under your account:")
    print(f"  - bucket {bid}  (private)")
    print(f"      seeded from hf://datasets/{FIXTURES_DATASET}/")
    print(f"  - Space  {bid}  (private, docker, hf_oauth)")
    print(f"      volume: hf://buckets/{bid} -> /data/inspector-bucket")
    print("      secrets: HF_TOKEN, INSPECTOR_SESSION_SECRET")
    print(f"      variable: INSPECTOR_BUCKET_REPO={bid}")
    print(f"  - deploy code: {'yes' if deploy else 'no (run deploy_space.py later)'}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "name", help="Short suffix for your env, e.g. 'alice' -> quranic-inspector-alice"
    )
    p.add_argument("--deploy", action="store_true", help="Also push code to the Space now.")
    p.add_argument(
        "--public-bucket", action="store_true", help="Make the bucket public (default: private)."
    )
    p.add_argument("--teardown", action="store_true", help="Delete the bucket + Space.")
    p.add_argument("--dry-run", action="store_true", help="Print the plan; change nothing.")
    args = p.parse_args(argv)

    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit(f"name must be alphanumeric (got {args.name!r})")

    token = _resolve_token()
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    user = _whoami(api)

    if args.dry_run:
        _print_plan(user=user, name=args.name, deploy=args.deploy, teardown=args.teardown)
        return 0

    if args.teardown:
        _teardown(api, user=user, name=args.name)
    else:
        try:
            _provision(
                api, user=user, name=args.name, deploy=args.deploy, public_bucket=args.public_bucket
            )
        except Exception:
            # Provisioning is multi-step; a mid-flight failure can leave a
            # partially-created bucket/Space. Don't auto-delete (the bucket may
            # hold data), but point at the idempotent re-run and the teardown.
            print(
                f"\n!! Provisioning failed partway. Re-run "
                f"`bootstrap_dev_env.py {args.name}` to resume (idempotent), or "
                f"`bootstrap_dev_env.py {args.name} --teardown` to remove what "
                f"was created.",
                file=sys.stderr,
            )
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
