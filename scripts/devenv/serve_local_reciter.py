#!/usr/bin/env python3
"""Serve one locally-held reciter through the Inspector, fully offline.

Points the ``filesystem`` storage backend at a directory laid out like a bucket
(``reciters/<slug>/{detailed.json,segments.json,audio/*.mp3,...}``), seeds the
catalog + state rows the UI needs for that slug, and starts the Flask app as a
synthetic owner (``INSPECTOR_DEV_MODE=1``; switch role with the in-app role
switcher).

Usage::

    python scripts/devenv/serve_local_reciter.py --root D:/bucket --slug my_reciter
    python scripts/devenv/serve_local_reciter.py --root D:/bucket --slug my_reciter --vite
    python scripts/devenv/serve_local_reciter.py --root D:/bucket --slug my_reciter --seed-only

What it seeds (idempotent — existing rows are left alone):

* vocab rows (riwayah / style / source / channel) the delivery FK chain needs
* ``reciters`` + ``deliveries`` rows for the slug (``by_surah``, chapter count
  from ``detailed.json``)
* a ``delivery_states`` row (default ``awaiting_review`` — editable in the
  Segments tab)
* ``catalog/audio_manifest/<slug>.json`` mapping every ``audio/<ch>.mp3`` to a
  bucket-path URL so the audio proxy serves the local file (only when absent)

The DB lives at ``<root>/db/inspector.db`` (the backend's copy) with the working
file at ``<root>/.local/inspector.db``. ``--vite`` also starts the Vite dev
server (HMR) proxied to the backend port; without it, build the frontend once
(``cd inspector/frontend && npm run build``) and open the Flask port directly.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSPECTOR = _REPO_ROOT / "inspector"
_FRONTEND = _INSPECTOR / "frontend"

DEFAULT_PORT = 5000
DEFAULT_VITE_PORT = 5173
DEFAULT_STATE = "awaiting_review"
LOCAL_VOCAB = {
    "riwayah": ("hafs_an_asim", "hafs", "Hafs A'n Assem"),
    "style": ("murattal", "murattal", "Murattal"),
    "source": ("local", "Local files"),
    "channel": ("local", "local", "Local files"),
}
_AUDIO_FILE_RE = re.compile(r"^(\d{1,3})\.mp3$")


def _env_for(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "INSPECTOR_BACKEND": "filesystem",
            "INSPECTOR_FILESYSTEM_ROOT": str(root),
            "INSPECTOR_DB_PATH": str(root / ".local" / "inspector.db"),
            "INSPECTOR_AUTO_MOUNT": "0",
            "INSPECTOR_AUDIO_FROM_BUCKET": "1",
            "INSPECTOR_PEAKS_FROM_BUCKET": "1",
            "INSPECTOR_DEV_MODE": "1",
            "INSPECTOR_RELEASE_POLL": "0",
        }
    )
    env.pop("INSPECTOR_BUCKET_MOUNT", None)
    env.pop("INSPECTOR_BEHIND_PROXY", None)
    return env


def _activate(env: dict[str, str]) -> None:
    os.environ.update(env)
    for p in (_REPO_ROOT, _INSPECTOR):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _chapters_in_detailed(reciter_dir: Path) -> list[int]:
    from services.storage.data_loader import load_detailed
    from utils.references import chapter_from_ref

    entries = load_detailed(reciter_dir.name)
    if not entries:
        raise SystemExit(f"no segments in {reciter_dir / 'detailed.json'}")
    return sorted({chapter_from_ref(e.get("ref", "")) for e in entries})


def _seed_catalog(slug: str, name: str, chapter_count: int) -> None:
    from qua_shared.schemas import Channel, Delivery, ReciterEntry, Riwayah, Source, Style, Vocab
    from qua_shared.schemas.bucket.catalog import AudioCategory
    from services.db import repo_catalog
    from services.db import sync as _sync

    riwayah, style, source, channel = (
        LOCAL_VOCAB["riwayah"],
        LOCAL_VOCAB["style"],
        LOCAL_VOCAB["source"],
        LOCAL_VOCAB["channel"],
    )
    vocab = Vocab(
        riwayat=[Riwayah(slug=riwayah[0], short=riwayah[1], name=riwayah[2])],
        styles=[Style(slug=style[0], short=style[1], name=style[2])],
        sources=[
            Source(slug=source[0], name=source[1], audio_categories=[AudioCategory("by_surah")])
        ],
        channels=[Channel(slug=channel[0], short=channel[1], name=channel[2])],
    )
    with _sync.durable_transaction():
        repo_catalog.load_vocab(vocab)
        if repo_catalog.find_reciter(slug) is None:
            repo_catalog.insert_reciter(ReciterEntry(reciter_id=slug, name_en=name))
            print(f"==> seeded reciter {slug!r}")
        if repo_catalog.find_delivery(slug) is None:
            repo_catalog.add_delivery(
                Delivery(
                    slug=slug,
                    reciter_id=slug,
                    riwayah=riwayah[0],
                    style=style[0],
                    source=source[0],
                    channel=channel[0],
                    audio_category=AudioCategory("by_surah"),
                    chapter_count=chapter_count,
                    added_at=datetime.now(UTC),
                    added_by_hf_id="serve_local_reciter",
                )
            )
            print(f"==> seeded delivery {slug!r} ({chapter_count} chapters)")


def _seed_state(slug: str, state: str) -> None:
    from qua_shared.schemas import ReciterState
    from services.db import repo_state
    from services.db import sync as _sync

    if repo_state.exists(slug):
        return
    with _sync.durable_transaction():
        repo_state.upsert_state(slug, state=ReciterState(state), state_since=datetime.now(UTC))
    print(f"==> seeded state {slug!r} -> {state}")


def _seed_audio_manifest(root: Path, slug: str) -> None:
    from qua_shared.schemas import AudioManifestSidecar
    from services.storage import storage_paths
    from services.storage.hf_bucket import get_backend

    path = storage_paths.audio_manifest_path(slug)
    if get_backend().exists(path):
        return
    audio_dir = root / "reciters" / slug / "audio"
    chapters: dict[str, dict] = {}
    for f in sorted(audio_dir.glob("*.mp3")) if audio_dir.is_dir() else []:
        m = _AUDIO_FILE_RE.match(f.name)
        if m:
            ch = str(int(m.group(1)))
            chapters[ch] = {"url": storage_paths.prefetched_audio_path(slug, ch)}
    doc = AudioManifestSidecar.model_validate(
        {
            "slug": slug,
            "_meta": {
                "checksum": "sha256:local",
                "chapter_count": len(chapters),
                "category": "by_surah",
            },
            "chapters": chapters,
        }
    )
    get_backend().write_json_atomic(path, doc.model_dump(mode="json", by_alias=True))
    print(f"==> wrote {path} ({len(chapters)} local chapters)")


def seed(root: Path, slug: str, name: str, state: str) -> None:
    from services import db
    from services.db import sync as _sync

    _sync.pull()
    db.init_db()
    chapters = _chapters_in_detailed(root / "reciters" / slug)
    _seed_catalog(slug, name, len(chapters))
    _seed_state(slug, state)
    _seed_audio_manifest(root, slug)


def _start_vite(env: dict[str, str], port: int, backend_port: int) -> subprocess.Popen:
    vite_env = dict(env)
    vite_env.update(
        {
            "INSPECTOR_VITE_PORT": str(port),
            "INSPECTOR_BACKEND_PORT": str(backend_port),
            "INSPECTOR_BACKEND_HOST": "127.0.0.1",
        }
    )
    vite_env.pop("INSPECTOR_API_TARGET", None)
    cmd = ["npm", "run", "dev", "--", "--port", str(port)]
    return subprocess.Popen(cmd, cwd=_FRONTEND, env=vite_env, shell=os.name == "nt")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--root", required=True, type=Path, help="bucket-shaped directory")
    p.add_argument("--slug", required=True, help="reciter slug under <root>/reciters/")
    p.add_argument("--name", default=None, help="display name (default: from slug)")
    p.add_argument(
        "--state", default=DEFAULT_STATE, help=f"lifecycle state (default {DEFAULT_STATE})"
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--vite", action="store_true", help="also start the Vite dev server (HMR)")
    p.add_argument("--vite-port", type=int, default=DEFAULT_VITE_PORT)
    p.add_argument("--seed-only", action="store_true", help="seed and exit without serving")
    args = p.parse_args(argv)

    root = args.root.resolve()
    reciter_dir = root / "reciters" / args.slug
    if not (reciter_dir / "detailed.json").is_file():
        print(f"ERROR: {reciter_dir / 'detailed.json'} not found", file=sys.stderr)
        return 2

    env = _env_for(root)
    name = args.name or args.slug.replace("_", " ").title()
    if args.seed_only:
        _activate(env)
        seed(root, args.slug, name, args.state)
        return 0

    # Seed in a child process so every SQLite handle is released before the
    # app pulls the DB (Windows refuses to replace an open file).
    seed_cmd = [sys.executable, __file__, "--root", str(root), "--slug", args.slug]
    seed_cmd += ["--name", name, "--state", args.state, "--seed-only"]
    subprocess.check_call(seed_cmd, env=env)

    vite = _start_vite(env, args.vite_port, args.port) if args.vite else None
    url = f"http://localhost:{args.vite_port if vite else args.port}"
    print(f"==> serving {args.slug!r} from {root} as synthetic owner -> {url}")
    try:
        return subprocess.call(
            [sys.executable, str(_INSPECTOR / "app.py"), "--port", str(args.port)], env=env
        )
    finally:
        if vite is not None:
            vite.terminate()


if __name__ == "__main__":
    sys.exit(main())
