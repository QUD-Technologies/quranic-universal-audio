#!/usr/bin/env python3
"""Maintainer-only: build the public *fixtures* dataset that powers Tier-0
offline onboarding (``seed_fixtures.py``) and seeds personal dev buckets
(``bootstrap_dev_env.py``).

The fixtures are a MINIMAL, PII-FREE slice of the real data:

* A **fresh** ``inspector.db`` — built from an empty schema, never copied from
  the live DB. Only an allowlist of catalog/vocabulary/state rows for the
  chosen reciters is copied in; the live DB's identity tables (``users``,
  ``transitions``, ``claims``, ``requests``, ``role_assignments``,
  ``activity_*``, ``visitor_daily``) are NEVER read. A fail-safe allowlist
  means a future table can't leak by default.
* Per-reciter content for the chosen reciters: ``detailed.json`` + sidecar
  JSON (``pipeline_meta``/``low_confidence_v2``/``auto_split_v1``), any
  ``timestamps/`` shards, and the audio manifest. Excluded: ``audio/`` and
  ``peaks/`` (heavy), and ``edit_history*.jsonl`` (heavy + holds actor PII).
  Legacy top-level dirs (``wip/``, ``published/``, ``access/``, ``audit/``,
  ``state/``, …) are never touched — fixtures only carry ``db/``,
  ``reciters/<slug>/`` and ``catalog/audio_manifest/<slug>.json``.

The result is staged locally. Publishing to the public dataset is a separate,
explicit ``--publish`` step (outward-facing, hard to reverse).

Usage::

    # Build locally, inspect what would ship (no network writes)
    python scripts/devenv/make_fixtures_dataset.py --reciters slug_a,slug_b

    # Build then publish to the public dataset
    python scripts/devenv/make_fixtures_dataset.py --reciters slug_a,slug_b --publish

Source bucket defaults to dev. Run from the repo root.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_INSPECTOR = _REPO_ROOT / "inspector"
if str(_INSPECTOR) not in sys.path:
    sys.path.insert(0, str(_INSPECTOR))

DEFAULT_DATASET = os.environ.get("INSPECTOR_FIXTURES_DATASET", "QUD-Technologies/quranic-inspector-fixtures")

# Vocabulary tables — copied wholesale (small, no PII, needed for catalog FKs).
_VOCAB_TABLES = ("riwayahs", "styles", "sources", "channels", "recording_contexts")
# Catalog/state tables — copied only for the chosen reciters (see _build_db).
# Everything NOT named here (users, transitions, claims, requests,
# role_assignments, activity_*, visitor_daily, db_meta, catalog_meta) is left
# exactly as a fresh ``init_db()`` created it: empty / default. Fail-safe.
_SELECTIVE_TABLES = ("reciters", "deliveries", "delivery_states", "catalog_aliases")

# State assigned to a chosen reciter that has no delivery_states row in the
# source DB. Editable (opens in the Segments editor) and matches what aligned
# reciters carry in the dev bucket.
_DEFAULT_FIXTURE_STATE = "awaiting_review"

# Per-reciter JSON pulled into the fixtures bucket tree (NO audio/, NO peaks/).
_RECITER_FILES = (
    "detailed.json",
    "segments.json",
    "low_confidence_v2.json",
    "auto_split_v1.json",
    "pipeline_meta.json",
)


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _copy_rows(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    where: str = "",
    params: tuple = (),
    scrub: dict | None = None,
) -> int:
    """Schema-agnostic row copy with optional per-column scrubbing.

    Skips silently if the table is missing on either side (tolerant of schema
    drift). ``scrub`` maps column name -> replacement value, applied to every
    row before insert — used to drop the lone identity field on catalog rows
    and to NULL dangling references to the (excluded) ``transitions`` table so
    foreign-key enforcement stays ON.
    """
    if not (_table_exists(src, table) and _table_exists(dst, table)):
        print(f"   - skip {table} (absent on one side)")
        return 0
    cols = _table_columns(src, table)
    rows = src.execute(f"SELECT {','.join(cols)} FROM {table} {where}", params).fetchall()
    if not rows:
        return 0
    if scrub:
        scrub_idx = {cols.index(c): v for c, v in scrub.items() if c in cols}
        if scrub_idx:
            rows = [tuple(scrub_idx.get(i, val) for i, val in enumerate(row)) for row in rows]
    placeholders = ",".join("?" * len(cols))
    dst.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def build_fixtures_db(src_db_path: Path, out_db_path: Path, slugs: list[str]) -> dict:
    """Build a fresh, PII-free fixtures DB containing only *slugs*.

    Testable core: open the source DB read-only, init a fresh schema at
    *out_db_path*, copy the vocab tables wholesale and the catalog/state rows
    for the chosen slugs. Returns a per-table row-count summary.
    """
    from services import db as _db

    if out_db_path.exists():
        out_db_path.unlink()
    out_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Fresh schema at the output path (empty tables — no PII).
    _db.set_db_path_for_test(out_db_path)
    _db.init_db()
    dst = _db.get_writer()

    src = sqlite3.connect(f"file:{src_db_path}?mode=ro", uri=True)
    counts: dict[str, int] = {}
    try:
        # Resolve the reciters behind the chosen deliveries.
        qmarks = ",".join("?" * len(slugs))
        reciter_ids = [
            r[0]
            for r in src.execute(
                f"SELECT DISTINCT reciter_id FROM deliveries WHERE slug IN ({qmarks})",
                tuple(slugs),
            ).fetchall()
        ]
        found_slugs = [
            r[0]
            for r in src.execute(
                f"SELECT slug FROM deliveries WHERE slug IN ({qmarks})", tuple(slugs)
            ).fetchall()
        ]
        missing = sorted(set(slugs) - set(found_slugs))
        if missing:
            raise SystemExit(f"slug(s) not found in source catalog: {', '.join(missing)}")

        for t in _VOCAB_TABLES:
            counts[t] = _copy_rows(src, dst, t)

        rid_marks = ",".join("?" * len(reciter_ids))
        counts["reciters"] = _copy_rows(
            src,
            dst,
            "reciters",
            f"WHERE reciter_id IN ({rid_marks})",
            tuple(reciter_ids),
        )
        # Scrub the lone identity field on the otherwise-public catalog row.
        counts["deliveries"] = _copy_rows(
            src,
            dst,
            "deliveries",
            f"WHERE slug IN ({qmarks})",
            tuple(slugs),
            scrub={"added_by_hf_id": "seed"},
        )
        # NULL the reference to the (excluded) transitions table so FK
        # enforcement — which is ON — doesn't reject the row.
        counts["delivery_states"] = _copy_rows(
            src,
            dst,
            "delivery_states",
            f"WHERE slug IN ({qmarks})",
            tuple(slugs),
            scrub={"created_by_transition_id": None},
        )
        # catalog_aliases.kind is 'slug' | 'reciter_id' with old/new values —
        # only carry aliases whose target is one of our reciters.
        if reciter_ids:
            counts["catalog_aliases"] = _copy_rows(
                src,
                dst,
                "catalog_aliases",
                f"WHERE new IN ({rid_marks})",
                tuple(reciter_ids),
            )
        # The source DB may lack a delivery_states row for some chosen
        # reciters (catalogued-but-never-projected gaps). Synthesize an
        # editable state for any that are missing so every fixtures reciter
        # opens in the Segments editor rather than rendering stateless.
        have_state = {r[0] for r in dst.execute("SELECT slug FROM delivery_states")}
        synthesized = 0
        for slug in found_slugs:
            if slug in have_state:
                continue
            row = dst.execute("SELECT added_at FROM deliveries WHERE slug=?", (slug,)).fetchone()
            since = row[0] if row and row[0] else "2024-01-01T00:00:00+00:00"
            dst.execute(
                "INSERT INTO delivery_states (slug, state, state_since, visibility) "
                "VALUES (?, ?, ?, 'public')",
                (slug, _DEFAULT_FIXTURE_STATE, since),
            )
            synthesized += 1
        if synthesized:
            counts["delivery_states_synthesized"] = synthesized
        dst.commit()
        # Collapse the WAL into a standalone inspector.db. The app's bucket
        # pull reads ONLY db/inspector.db and discards -wal/-shm, so any data
        # left in the WAL would vanish on first boot. DELETE mode checkpoints
        # and drops the sidecar, yielding a self-contained file safe to ship.
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        src.close()

    # Defensively remove any leftover sidecars next to the output DB.
    for side in (f"{out_db_path}-wal", f"{out_db_path}-shm"):
        try:
            Path(side).unlink()
        except OSError:
            pass
    return {"slugs": found_slugs, "reciter_ids": reciter_ids, "rows": counts}


def _copy_bucket_content(backend, slugs: list[str], stage_root: Path) -> dict:
    """Pull per-reciter JSON (no audio/peaks) + audio manifest into the stage."""
    summary: dict[str, int] = {}
    for slug in slugs:
        try:
            backend.read_bytes(f"reciters/{slug}/detailed.json")
        except Exception:
            print(f"   ! {slug}: no detailed.json under reciters/ — skipping content")
            continue
        n = 0
        for fname in _RECITER_FILES:
            try:
                data = backend.read_bytes(f"reciters/{slug}/{fname}")
            except Exception:
                continue
            dst = stage_root / "reciters" / slug / fname
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            n += 1
        # Timestamps shards (released reciters only) — compact per-chapter Brotli JSON
        # that drives the Timestamps tab. No-op for non-released reciters.
        try:
            shards = backend.list_dir(f"reciters/{slug}/timestamps") or []
        except Exception:
            shards = []
        for shard in shards:
            name = Path(shard).name
            if not name.endswith(".json.br"):
                continue
            try:
                data = backend.read_bytes(f"reciters/{slug}/timestamps/{name}")
            except Exception:
                continue
            dst = stage_root / "reciters" / slug / "timestamps" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            n += 1
        # Audio manifest (gives the editor CDN audio URLs without bundling mp3s).
        try:
            man = backend.read_bytes(f"catalog/audio_manifest/{slug}.json")
            dst = stage_root / "catalog" / "audio_manifest" / f"{slug}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(man)
        except Exception:
            print(f"   ! {slug}: no audio_manifest — audio won't stream in fixtures")
        summary[slug] = n
        print(f"   - {slug}: {n} content file(s)")
    return summary


def _publish(stage_root: Path, dataset_id: str, token: str) -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=dataset_id, repo_type="dataset", private=False, exist_ok=True)
    api.upload_folder(
        folder_path=str(stage_root),
        repo_id=dataset_id,
        repo_type="dataset",
        commit_message="update inspector fixtures",
        delete_patterns="*",
    )
    return f"https://huggingface.co/datasets/{dataset_id}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    p.add_argument("--reciters", required=True, help="Comma-separated delivery slug(s) to include.")
    p.add_argument(
        "--bucket",
        choices=("dev", "prod"),
        default="dev",
        help="Source bucket to read catalog + content from.",
    )
    p.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"Target public dataset (default {DEFAULT_DATASET}).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / ".local" / "fixtures-stage",
        help="Local staging dir for the dataset tree.",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="Upload the staged tree to the public dataset (outward-facing).",
    )
    args = p.parse_args(argv)

    slugs = [s.strip() for s in args.reciters.split(",") if s.strip()]
    if not slugs:
        raise SystemExit("--reciters must list at least one slug")

    from qua_shared._env import load_repo_env

    load_repo_env()
    buckets = {
        "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
        "prod": "QUD-Technologies/quranic-inspector-bucket",
    }
    os.environ["INSPECTOR_BUCKET_REPO"] = buckets[args.bucket]
    if args.bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"  # read-only source

    from services.storage.hf_bucket import get_backend

    backend = get_backend()

    stage_root = args.out.resolve()
    if stage_root.exists():
        import shutil

        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    print(f"==> Reading source DB from bucket {buckets[args.bucket]}")
    src_bytes = backend.read_bytes("db/inspector.db")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        tf.write(src_bytes)
        src_db_path = Path(tf.name)

    try:
        print(f"==> Building fresh PII-free DB for: {', '.join(slugs)}")
        result = build_fixtures_db(src_db_path, stage_root / "db" / "inspector.db", slugs)
        print(f"   rows: {result['rows']}")
        print("==> Copying per-reciter content (no audio/peaks)")
        _copy_bucket_content(backend, result["slugs"], stage_root)
    finally:
        try:
            src_db_path.unlink()
        except OSError:
            pass

    print(f"\n==> Staged fixtures tree at {stage_root}")
    if not args.publish:
        print(f"    (build only) re-run with --publish to upload to {args.dataset}")
        return 0

    token = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN required to --publish")
    print(f"==> Publishing to public dataset {args.dataset}")
    url = _publish(stage_root, args.dataset, token)
    print(f"==> Done. {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
