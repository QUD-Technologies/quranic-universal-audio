"""Verify a staged generation run and publish it as ``reciters/<slug>/``.

    python scripts/bucket/promote_run.py <slug>/<run-id> --bucket dev [--force]
    python scripts/bucket/promote_run.py <slug>/<run-id> --dry-run --compare <slug>

The batch pipeline stages a run under ``staging/<slug>/<run-id>/``: one
``candidates/<ch>.json`` per chapter, ``events.json``, ``chapter_sources.json``,
``coverage_report.json``, the chapter mp3s, optional sidecars, and a
``manifest.json`` that names, sizes and hashes every one of them. This script is
the only thing that turns that into a published reciter folder.

``_promote_artifacts.py`` is the build half — verification and every
Inspector-side derivation. This module owns the side effects: the refusals, the
one-batch publish with ``audio/_done.json`` last so a half-written reciter never
reads as prefetched, the staging teardown, and the closing audit. ``--dry-run``
stops after the build; ``--compare <slug>`` then diffs the built artifacts
against that reciter's published ones, which is the parity-replay surface and
needs no bucket write.

Promote never calls ``state.transition``: Inspector's ``auto_detect`` sees the
new folder within ~60 s and fires ``reciter.alignment_completed`` itself, from
inside the single writer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "inspector"), str(Path(__file__).parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _bootstrap as bs  # noqa: E402
import _promote_artifacts as pa  # noqa: E402

_RECITERS = "reciters"
_DONE_SENTINEL = "audio/_done.json"
_PIPELINE_ACTOR_ID = "pipeline"

# States a slug may be in when promote overwrites it. Anything later carries
# human review work that a fresh publish would silently discard.
_PROMOTABLE_STATES = ("catalogued", "awaiting_alignment")

# Clock-derived fields, dropped before a --compare diff.
_COMPARE_DROP = {
    "detailed.json": (("_meta", "created_at"),),
    "segments.json": (("_meta", "created_at"),),
    # deleted_basmala_chapters: legacy key old sidecars carry; promote no longer writes it.
    "pipeline_meta.json": (("generated_at",), ("deleted_basmala_chapters",)),
}
_COMPARE_JSON = ("detailed.json", "segments.json", "pipeline_meta.json", "chapter_sources.json")
_MAX_REPORTED_DIFFS = 10


def _abort(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _setup_env(bucket: str) -> str:
    """Pin the bucket and the script-private DB path; return the bucket id.

    Runs before the first inspector import: ``get_backend()`` caches a
    process-wide singleton off ``INSPECTOR_BUCKET_REPO``. The DB is only ever
    read here (the state-row guard), so write-back is disarmed and the SQLite
    file goes somewhere a running local Inspector is not holding open.
    """
    bs.ensure_utf8_stdout()
    if not bs.load_hf_token():
        print("warning: HF_TOKEN not in env or .env — bucket calls may 401", file=sys.stderr)
    bucket_id = bs.BUCKETS[bucket]
    os.environ["INSPECTOR_BACKEND"] = "bucket"
    os.environ["INSPECTOR_BUCKET_REPO"] = bucket_id
    if bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    os.environ["INSPECTOR_DB_SYNC"] = "0"
    os.environ.setdefault(
        "INSPECTOR_DB_PATH", str(Path(tempfile.gettempdir()) / f"promote_run_{bucket}.db")
    )
    return bucket_id


# ---------------------------------------------------------------------------
# The target guards
# ---------------------------------------------------------------------------


def _human_ops_present(backend, slug: str) -> bool:
    """True if ``reciters/<slug>/edit_history.jsonl`` has a non-pipeline actor."""
    path = f"{_RECITERS}/{slug}/edit_history.jsonl"
    if not backend.exists(path):
        return False
    for line in backend.read_bytes(path).decode("utf-8").splitlines():
        if not line.strip():
            continue
        actor = (json.loads(line).get("actor") or {}).get("hf_user_id")
        if actor and actor != _PIPELINE_ACTOR_ID:
            return True
    return False


def _guard_riwayah(slug: str, manifest) -> None:
    """Refuse a run whose edition is not the one the catalog claims for the slug."""
    from qua_shared.riwayat import UnsupportedRiwayah, resolve_sdk_slug
    from services.reference.delivery_edition import inspector_riwayah_for

    try:
        catalogued = resolve_sdk_slug(inspector_riwayah_for(slug))
    except UnsupportedRiwayah as exc:
        _abort(f"{slug}: {exc}")
    if catalogued != manifest.inputs.riwayah:
        _abort(
            f"{slug}: the run aligned {manifest.inputs.riwayah!r} but the catalog "
            f"delivery is {catalogued!r} — fix the delivery row or re-run the "
            "alignment; --force does not cover this."
        )
    print(f"  riwayah: {catalogued}")


def guard_target(backend, slug: str, force: bool, manifest) -> None:
    """Refuse a promote that would overwrite reviewed or already-published work.

    A slug past ``AWAITING_ALIGNMENT`` needs ``--force``, and ``--force`` is
    still refused when a human has edited the reciter — promote a new slug or a
    new run instead. The two remote checks mirror the retiring uploader's: an
    A/B's ``<slug>`` / ``<slug>-bnd`` pair is exactly the mistyped-slug hazard
    they guard.

    The riwayah check is not overridable by ``--force``: publishing an alignment
    made against one edition under a catalog row claiming another gives every
    downstream reader — script, word counts, timestamps, releases — the wrong
    edition, and no later edit can tell which half was wrong.
    """
    from services import db as _db
    from services.db import sync as _db_sync
    from services.state import state as state_svc

    _db_sync.pull()
    _db.init_db()
    _guard_riwayah(slug, manifest)
    row = state_svc.get_row(slug)
    if row is not None and row.state.value not in _PROMOTABLE_STATES:
        if not force:
            _abort(
                f"{slug} is {row.state.value}, past awaiting_alignment — a promote would "
                "discard review work. Promote a new slug or a new run, or pass --force."
            )
        if _human_ops_present(backend, slug):
            _abort(f"{slug} carries human edit-history operations — refusing even with --force.")

    detailed = f"{_RECITERS}/{slug}/detailed.json"
    if backend.exists(detailed):
        if not force:
            _abort(
                f"target already exists: {detailed}\n"
                "auto_detect won't re-fire alignment_completed for a slug that has left "
                "awaiting_alignment. Pass --force to overwrite the bytes."
            )
        print(f"  --force: overwriting existing {_RECITERS}/{slug}/")
    sentinel = f"{_RECITERS}/{slug}/{_DONE_SENTINEL}"
    if backend.exists(sentinel) and not force:
        _abort(f"audio sentinel already exists: {sentinel}\nPass --force to overwrite.")


# ---------------------------------------------------------------------------
# Publish and tear down
# ---------------------------------------------------------------------------


def publish(bucket_id: str, backend, slug: str, run_dir: Path, built: dict[str, bytes]) -> int:
    """One Xet batch, then the sentinel. Returns the chapter count published.

    Chapter audio and baked peaks go in as ``Path``s — they are already on disk,
    and a full reciter is ~2.5 GB that no dict should have to hold.
    """
    base = f"{_RECITERS}/{slug}"
    files: dict[str, bytes | Path] = {f"{base}/{name}": body for name, body in built.items()}
    chapters = sorted(int(p.stem) for p in (run_dir / "audio").glob("*.mp3"))
    for chapter in chapters:
        files[f"{base}/audio/{chapter}.mp3"] = run_dir / "audio" / f"{chapter}.mp3"
        files[f"{base}/peaks/{chapter}.json.gz"] = run_dir / "peaks" / f"{chapter}.json.gz"
    print(f"  writing {len(files)} file(s) to {base}/")
    bs.batch_write(bucket_id, files)
    if chapters:
        sentinel = {
            "schema_version": 1,
            "total_chapters": len(chapters),
            "completed_at_ms": int(time.time() * 1000),
        }
        backend.write_bytes_atomic(f"{base}/{_DONE_SENTINEL}", pa.dumps(sentinel))
    return len(chapters)


def clear_staging(backend, slug: str, run_id: str, manifest) -> None:
    """Drop ``staging/<slug>/<run-id>/`` — the staging lifetime nobody else owns.

    The run directory on scratch is untouched; ``reap`` owns that.
    """
    prefix = f"{pa.STAGING_PREFIX}/{slug}/{run_id}"
    for rel in [a.path for a in manifest.artifacts] + [pa.MANIFEST_NAME]:
        try:
            backend.delete(f"{prefix}/{rel}")
        except Exception as e:  # noqa: BLE001 — a stale staging file is not a failed promote
            print(f"  warning: could not delete {prefix}/{rel}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# --compare — the parity-replay surface
# ---------------------------------------------------------------------------


def _drop(obj: Any, path: tuple[str, ...]) -> None:
    for key in path[:-1]:
        if not isinstance(obj, dict):
            return
        obj = obj.get(key)
    if isinstance(obj, dict):
        obj.pop(path[-1], None)


def _walk_diff(built: Any, published: Any, where: str, out: list[str]) -> None:
    if len(out) >= _MAX_REPORTED_DIFFS:
        return
    if isinstance(built, dict) and isinstance(published, dict):
        for key in sorted(set(built) | set(published)):
            if key not in built:
                out.append(f"{where}.{key}: only published")
            elif key not in published:
                out.append(f"{where}.{key}: only built")
            else:
                _walk_diff(built[key], published[key], f"{where}.{key}", out)
    elif isinstance(built, list) and isinstance(published, list):
        if len(built) != len(published):
            out.append(f"{where}: {len(built)} built vs {len(published)} published")
            return
        for i, (b, p) in enumerate(zip(built, published, strict=True)):
            _walk_diff(b, p, f"{where}[{i}]", out)
    elif built != published:
        out.append(f"{where}: {built!r} != {published!r}")


def _normalised_ops(batches: list[dict]) -> list[dict]:
    """Ops stripped of the per-run fields, ordered so two runs compare.

    ``op_id`` / ``batch_id`` / ``saved_at_utc`` are minted per run, and
    ``segment_uid`` is a uuid7 on a published pipeline row against a derived
    uuid5 here — so it is dropped and checked as an invariant instead.
    """
    keep = (
        "index_at_save",
        "chapter",
        "audio_url",
        "time_start",
        "time_end",
        "matched_ref",
        "confidence",
    )
    ops = [
        {
            "op_type": op.get("op_type"),
            "fix_kind": op.get("fix_kind"),
            **{
                side: [{k: s.get(k) for k in keep} for s in op.get(side, [])]
                for side in ("targets_before", "targets_after")
            },
        }
        for batch in batches
        for op in batch.get("operations", [])
    ]
    return sorted(ops, key=lambda o: json.dumps(o, sort_keys=True))


def _jsonl(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def _report(name: str, diffs: list[str]) -> int:
    if not diffs:
        print(f"  {name}: identical")
        return 0
    print(f"  {name}: {len(diffs)}{'+' if len(diffs) >= _MAX_REPORTED_DIFFS else ''} difference(s)")
    for line in diffs:
        print(f"      {line}")
    return 1


def compare(backend, slug: str, built: dict[str, bytes]) -> int:
    """Diff the built artifacts against ``reciters/<slug>/``; return the failures.

    Structural, not byte-level: clock-derived fields are dropped, edit-history
    batches are normalised, and per-op peaks are reported by count and url
    because their ``op_id``s are minted per run.
    """
    failures = 0
    for name in _COMPARE_JSON:
        if name not in built:
            continue
        try:
            theirs = json.loads(backend.read_bytes(f"{_RECITERS}/{slug}/{name}"))
        except Exception as e:  # noqa: BLE001 — an unreadable counterpart is a failed compare
            print(f"  {name}: not published ({e})")
            failures += 1
            continue
        mine = json.loads(built[name])
        for path in _COMPARE_DROP.get(name, ()):
            _drop(mine, path)
            _drop(theirs, path)
        diffs: list[str] = []
        _walk_diff(mine, theirs, name, diffs)
        failures += _report(name, diffs)

    if "edit_history.jsonl" in built:
        published = list(backend.iter_jsonl(f"{_RECITERS}/{slug}/edit_history.jsonl"))
        diffs = []
        _walk_diff(
            _normalised_ops(_jsonl(built["edit_history.jsonl"])),
            _normalised_ops(published),
            "edit_history",
            diffs,
        )
        failures += _report("edit_history.jsonl", diffs)

    if "edit_history_peaks.jsonl" in built:
        mine = _jsonl(built["edit_history_peaks.jsonl"])
        theirs = list(backend.iter_jsonl(f"{_RECITERS}/{slug}/edit_history_peaks.jsonl"))
        same = {r["url"] for r in mine} == {r["url"] for r in theirs}
        print(
            f"  edit_history_peaks.jsonl: {len(mine)} built vs {len(theirs)} published "
            f"record(s); urls {'match' if same else 'DIFFER'}"
        )
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run", help="staged run as <slug>/<run-id>")
    parser.add_argument("--force", action="store_true", help="overwrite an existing reciter")
    parser.add_argument("--dry-run", action="store_true", help="build everything, write nothing")
    parser.add_argument("--compare", metavar="SLUG", help="diff the build against a published slug")
    bs.add_bucket_args(parser)
    args = parser.parse_args()

    if args.compare and not args.dry_run:
        parser.error("--compare only makes sense with --dry-run")
    if args.run.count("/") != 1 or not all(args.run.split("/")):
        parser.error(f"expected <slug>/<run-id>, got {args.run!r}")
    slug, run_id = args.run.split("/")
    if not args.dry_run:
        bs.confirm_mutation(args, f"promote {slug}")

    bucket_id = _setup_env(args.bucket)
    from services.storage.hf_bucket import get_backend

    backend = get_backend()
    print(f"staged run: {pa.STAGING_PREFIX}/{slug}/{run_id}   bucket: {args.bucket} ({bucket_id})")

    with tempfile.TemporaryDirectory(prefix=f"promote-{slug}-") as tmp:
        run_dir = Path(tmp)
        try:
            manifest = pa.fetch_staged_run(backend, slug, run_id, run_dir)
            print(f"  manifest verified: {len(manifest.artifacts)} artifact(s)")
            built = pa.build_artifacts(run_dir, manifest, slug)
        except ValueError as e:
            _abort(str(e))

        if args.dry_run:
            if args.compare:
                print(f"\ncompare vs {_RECITERS}/{args.compare}/")
                return 1 if compare(backend, args.compare, built) else 0
            print("\n--dry-run: nothing written.")
            return 0

        guard_target(backend, slug, args.force, manifest)
        n_chapters = publish(bucket_id, backend, slug, run_dir, built)
        print(f"  published {n_chapters} chapter(s); sentinel written last")
        clear_staging(backend, slug, run_id, manifest)

    from services.storage.bucket_audit import audit

    result = audit(backend, bucket_id, slug)
    for f in result.files:
        print(f"  [{f.status:<7}] {f.path}  {f.detail or ''}")
    print(f"audit: {result.n_errors} error(s), {result.n_missing} missing")
    return 0 if result.n_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
