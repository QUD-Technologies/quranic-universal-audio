"""Validate every artifact in a bucket reciter folder against the canonical
``qua_shared/schemas`` definitions.

Thin CLI over ``services.storage.bucket_audit`` (the Flask-free engine that
owns all the per-file auditors + the ``reciters/<slug>/`` walker). This module
keeps only the command-line surface: the ``--local-path`` backend shim, the
human-readable renderer, sys.path/env bootstrap, and ``main()``.

Walks ``reciters/<slug>/`` on the configured bucket and audits every file we
expect to find there:

* ``detailed.json``            -> ``DetailedDocument`` (pydantic v2)
* ``segments.json``            -> structural check (no formal schema yet)
* ``edit_history.jsonl``       -> ``parse_edit_history_line`` (per JSONL row)
* ``edit_history_peaks.jsonl`` -> ``parse_peaks_record`` (per JSONL row)
* ``low_confidence_v2.json``   -> structural check (``failures`` is list[str])
* ``auto_split_v1.json``       -> structural check (``by_uid`` dict of slim entries)
* ``pipeline_meta.json``       -> ``PipelineMeta`` (pydantic v2)
* ``audio/_done.json``         -> sentinel parse
* ``audio/<ch>.mp3``           -> existence + non-empty (3-chapter sample)
* ``peaks/<ch>.json.gz``       -> v3 slim ``unpack_slim`` round-trip
                                   (3-chapter sample)

Output:
    per-file pass/fail with structured error details
    summary table at the end (incl. unknown-field WARNINGs and
    legacy-field INFO strips emitted by the schema extras pre-validator)
    exit-non-zero if any artifact failed

Two backends:
    --bucket prod|dev   audit against an HF bucket (default: prod)
    --local-path <dir>  audit a locally-staged slug folder, used as the
                        pre-upload verification step in the migration
                        workflow described below.

Run from repo root. Requires ``HF_TOKEN`` in env (or ``.env``) for private
buckets. Uses the inspector storage backend so private-bucket auth works
the same way Inspector itself does.

================================================================
Migration toolkit — download → migrate → audit-local → upload
================================================================

When auditing surfaces drift on a legacy reciter, the companion scripts
in this directory chain into a self-contained per-slug migration. The
canonical workflow used to land cohorts 1, 2, and 4 (May 2026) is:

1. **Download** the bucket-resident metadata + peaks (audio skipped by
   default — typically 10s-100s of MB and unchanged by a schema
   migration). Mirrors the bucket layout under the chosen ``--out`` dir::

       python3 scripts/bucket/download_bucket_reciter.py \
           --slug <slug> --bucket prod --out /tmp/<slug>/
           [--include-audio]

2. **Migrate** the three legacy JSON/JSONL artefacts in place. Drops
   every now-dead legacy key so the artefact round-trips through the
   canonical (pure ``extra="forbid"``) schemas; rounds
   float ms times; synthesises missing ``op_id`` via
   ``uuid5("legacy_op:{batch_id}:{idx}")``; drops the genesis record
   (both v0 ``type==genesis`` and v1 ``record_type==genesis``); re-encodes
   peaks_history records to ``peaks_b64``+``bps``::

       python3 .local/extraction/scripts/migrate_wip5_in_place.py \
           --scratch-base /tmp --slug <slug> --apply

   Originals back up to ``<slug>/.migrate_wip5_backup/`` first.

3. **Convert legacy peaks** (only if ``peaks/<ch>.json`` plain files are
   present — pre-#5 reciters): repack each via
   ``services.audio.peaks_slim.pack_slim`` to v3 slim gzip (98%+ saved)
   and delete the plain originals locally::

       python3 scripts/backfills/convert_peaks_v2_to_v3.py \
           --dir /tmp/<slug>/peaks/

4. **pipeline_meta.json** is produced by the offline extraction pipeline
   (qua-aligner-offline) and uploaded alongside the reciter's artefacts;
   it is no longer derived in this repo.

5. **Audit locally** — same script, ``--local-path`` mode. Pre-flight
   gate before any bucket write::

       python3 scripts/bucket/audit_bucket_reciter.py \
           --slug <slug> --local-path /tmp/<slug>/

6. **Upload** the migrated metadata + peaks back. Deletes orphan plain
   ``peaks/<ch>.json`` files left on the bucket from a pre-Migration #5
   layout (their ``.json.gz`` replacements were written in step 3)::

       python3 scripts/bucket/upload_bucket_reciter.py \
           --slug <slug> --src /tmp/<slug>/ --bucket prod --apply
           [--include-audio]

7. **Audit against the bucket** — same script, ``--bucket`` mode.
   Pass on ``15/15 ok, 0 errors, 0 warnings, 0 legacy strips``.

Cohort 1 (pipeline_meta only — segs already slim) used just step 4:
``scripts/backfills/backfill_deleted_basmala.py --slugs <comma_list>``
(reads ``edit_history.jsonl`` directly from the bucket via
``services/storage/data_dir.py``).

Common legacy edge cases handled by the migrator:

  - float ms in detailed.json + segments.json (saad_al_ghamdi)
  - ops missing ``op_id`` (mishary's v0 ``set_meta_field`` shape) →
    UUIDv5 backfill
  - v0 batch shape with ``save_mode`` + ``chapter`` and no ``actor``
    (nasser_al_qatami) → falls back to PIPELINE_ACTOR; ``save_mode`` is
    no longer persisted (the migrator drops it; the canonical batch
    model now forbids it)
  - v1 pipeline genesis records (``record_type==genesis``) → dropped
    silently by ``parse_edit_history_line``
  - 184 MB plain ``peaks/<ch>.json`` × 114 → 2.5 MB ``.json.gz`` v3 slim
    (98.7% reduction) via ``convert_peaks_v2_to_v3.py``

To onboard a NEW schema field cleanly without resurrecting the silent-
allow regression:

  1. Add the field to the relevant model in ``qua_shared/schemas/``.
  2. Run this audit on a representative slug. The bucket artefact models
     are pure ``extra="forbid"`` — any unknown field raises a
     ``ValidationError``, signalling a writer still emitting bloat (fix
     the writer) or stale on-disk data (run a migration pass).
  3. If the field replaces an old one, migrate the on-disk artefacts
     (round-trip through the schemas, as in the workflow above) so
     legacy data conforms — there are no per-model dead-field sets to
     update; forbid means old keys must be gone, not tolerated.
  4. Run ``scripts/codegen/regen_fe_types.py`` so the frontend types
     stay in lockstep (CI's ``schema-codegen-check`` enforces this).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("audit_bucket_reciter")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}


def _setup_paths_and_env(bucket: str | None) -> None:
    """Insert repo root + inspector/ onto sys.path; pin bucket env."""
    here = Path(__file__).resolve()
    repo = here.parents[2]
    inspector = repo / "inspector"
    if not inspector.is_dir():
        raise SystemExit(f"inspector/ not found at {inspector}")
    sys.path.insert(0, str(inspector))
    sys.path.insert(0, str(repo))
    if bucket is not None:
        os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]


class _LocalBackend:
    """Backend shim over a local reciter directory.

    Pre-upload verification path: lets ``audit_bucket_reciter`` run against
    a freshly-downloaded slug on disk so the migration can be inspected
    before any bytes hit the bucket.

    The local dir points at the slug folder itself (``<root>/...``); the
    audit walker probes ``reciters/<slug>/x`` paths, so we strip that prefix
    to map back onto the flat local layout.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read_bytes(self, rel: str) -> bytes:
        # Strip the reciters/<slug>/ prefix so the underlying local dir
        # doesn't need that nesting.
        rel = self._normalise(rel)
        p = self.root / rel
        if not p.is_file():
            raise FileNotFoundError(rel)
        return p.read_bytes()

    def list_dir(self, prefix: str) -> list[str]:
        prefix = self._normalise(prefix.rstrip("/"))
        d = self.root / prefix
        if not d.is_dir():
            return []
        return [f"{prefix}/{name}" for name in sorted(os.listdir(d))]

    def _normalise(self, rel: str) -> str:
        # When the audit walker probes ``reciters/<slug>/x`` and the local
        # root already represents that slug folder, drop the prefix.
        if rel.startswith("reciters/"):
            parts = rel.split("/", 2)
            if len(parts) >= 3:
                return parts[2]
        return rel


def _render(result) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"Reciter:  {result.slug}")
    lines.append(f"Bucket:   {result.bucket_id}")
    lines.append(f"Location: reciters/{result.slug}/")
    lines.append("=" * 72)
    status_icon = {"ok": "OK  ", "missing": "MISS", "error": "FAIL"}
    for f in result.files:
        icon = status_icon.get(f.status, "????")
        size_str = f"{f.size:>10,}B " if f.size else " " * 12
        lines.append(f"  [{icon}] {size_str} {f.path}")
        if f.detail:
            lines.append(f"            {f.detail}")
    lines.append("-" * 72)
    n_ok = sum(1 for f in result.files if f.status == "ok")
    n_warn_total = sum(f.n_warn for f in result.files)
    n_info_total = sum(f.n_info for f in result.files)
    lines.append(
        f"Summary: {n_ok}/{len(result.files)} ok  |  "
        f"{result.n_errors} error(s)  |  {result.n_missing} missing  |  "
        f"{n_warn_total} unknown-field warning(s)  |  "
        f"{n_info_total} legacy-field strip(s)"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slug", required=True, help="Reciter slug.")
    ap.add_argument(
        "--bucket",
        choices=sorted(_BUCKETS),
        default=None,
        help="Bucket to audit. Mutually exclusive with --local-path.",
    )
    ap.add_argument(
        "--local-path",
        type=Path,
        default=None,
        help="Audit a local copy of a reciter folder (pre-upload "
        "verification). Should point at the slug dir itself, "
        "e.g. /tmp/husary/. Mutually exclusive with --bucket.",
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit JSON instead of the human-readable table."
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.bucket is None and args.local_path is None:
        args.bucket = "prod"  # back-compat default
    if args.bucket is not None and args.local_path is not None:
        ap.error("--bucket and --local-path are mutually exclusive")

    _setup_paths_and_env(args.bucket)

    from services.storage.bucket_audit import audit

    if args.local_path is not None:
        backend = _LocalBackend(args.local_path)
        bucket_id = f"local:{args.local_path}"
    else:
        from services.storage.hf_bucket import get_backend

        backend = get_backend()
        bucket_id = _BUCKETS[args.bucket]
    result = audit(backend, bucket_id, args.slug)

    if args.json:
        out = {
            "slug": result.slug,
            "bucket": result.bucket_id,
            "found": result.found,
            "files": [
                {
                    "path": f.path,
                    "status": f.status,
                    "detail": f.detail,
                    "size": f.size,
                    "items_ok": f.items_ok,
                    "items_total": f.items_total,
                }
                for f in result.files
            ],
            "n_ok": sum(1 for f in result.files if f.status == "ok"),
            "n_error": result.n_errors,
            "n_missing": result.n_missing,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(_render(result))

    return 0 if result.n_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
