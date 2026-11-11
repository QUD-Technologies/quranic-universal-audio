"""Whole-bucket external-artefact validator — the steady-state drift gate.

Walks an entire bucket and validates every external file we expect to find
against the canonical ``qua_shared/schemas`` definitions, so catalog /
audio-manifest / per-reciter-artefact / DB drift is caught in CI and at
deploy rather than at read time in front of a user.

Three validation passes:

1. **Per-reciter artefacts** — for every slug under ``reciters/``, run
   ``services.storage.bucket_audit.audit`` (the Flask-free engine that owns
   the per-file auditors) and aggregate hard errors + legacy-field INFO strips
   + unknown-field WARNINGs.
2. **DB catalog** — ``services.db.repo_catalog.snapshot()`` rebuilds +
   validates the ``ReciterCatalog`` from the DB rows (raises ``ValidationError``
   on a bad row). The dead ``catalog/reciter_catalog.json`` backup is NOT
   validated — no app reads it.
3. **Audio-manifest sidecars** — every ``catalog/audio_manifest/<slug>.json``
   is parsed through ``qua_shared.schemas.AudioManifestSidecar``.

Output:
    per-area summary + a grand total. ``--json`` emits the machine form.
    Exit non-zero when ANY artefact hard-errors (schema fail / structural
    reject / missing-required), so CI fails on drift. Legacy strips (INFO)
    and unknown fields (WARNING) are reported but don't fail by default;
    pass ``--strict`` to also fail when any unknown field is seen.

Two backends:
    --bucket prod|dev   validate against the configured HF bucket (default dev).
                        Prod reads need ``INSPECTOR_ALLOW_PROD_BUCKET=1`` (a
                        safety guard — fine for a read-only nightly job).

Run from repo root. Requires ``HF_TOKEN`` in env (or ``.env``) for private
buckets. The shared validation logic lives in
``services.storage.bucket_audit`` + ``services.db.repo_catalog`` +
``qua_shared.schemas`` — this module is the thin whole-bucket CLI over them.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("validate_bucket")

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

_AUDIO_MANIFEST_PREFIX = "catalog/audio_manifest"


def _setup_paths_and_env(bucket: str | None) -> None:
    """Insert repo root + inspector/ onto sys.path; pin the bucket env.

    Pins ``INSPECTOR_DB_PATH`` to an isolated per-bucket temp file so the
    catalog pass can pull the bucket DB into it (see ``validate_catalog``)
    without ever clobbering a working ``inspector.db`` on a manual run.
    """
    here = Path(__file__).resolve()
    repo = here.parents[2]
    inspector = repo / "inspector"
    if not inspector.is_dir():
        raise SystemExit(f"inspector/ not found at {inspector}")
    sys.path.insert(0, str(inspector))
    sys.path.insert(0, str(repo))
    if bucket is not None:
        os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
        os.environ["INSPECTOR_DB_PATH"] = str(
            Path(tempfile.gettempdir()) / f"validate_bucket_{bucket}.db"
        )


# ---------------------------------------------------------------------------
# Result aggregation
# ---------------------------------------------------------------------------


@dataclass
class ReciterReport:
    """Roll-up of one reciter's ``audit()`` result."""

    slug: str
    found: bool
    n_errors: int = 0
    n_missing: int = 0
    n_legacy: int = 0  # INFO-level legacy-field strips
    n_warnings: int = 0  # WARNING-level unknown fields
    error_details: list[str] = field(default_factory=list)


@dataclass
class SidecarReport:
    """Outcome of validating one ``audio_manifest/<slug>.json``."""

    slug: str
    ok: bool
    detail: str = ""


@dataclass
class BucketReport:
    """Whole-bucket validation roll-up; drives the CLI exit code."""

    bucket_id: str
    reciters: list[ReciterReport] = field(default_factory=list)
    sidecars: list[SidecarReport] = field(default_factory=list)
    catalog_ok: bool = True
    catalog_detail: str = ""
    catalog_checked: bool = True

    # ---- aggregates ----

    @property
    def n_reciter_errors(self) -> int:
        return sum(r.n_errors for r in self.reciters)

    @property
    def n_legacy(self) -> int:
        return sum(r.n_legacy for r in self.reciters)

    @property
    def n_warnings(self) -> int:
        return sum(r.n_warnings for r in self.reciters)

    @property
    def n_sidecar_errors(self) -> int:
        return sum(1 for s in self.sidecars if not s.ok)

    @property
    def n_hard_errors(self) -> int:
        """Schema-fail / structural-reject count across all three passes."""
        errs = self.n_reciter_errors + self.n_sidecar_errors
        if not self.catalog_ok:
            errs += 1
        return errs

    def exit_code(self, *, strict: bool) -> int:
        """Non-zero when any artefact hard-errors; ``strict`` also fails on
        unknown-field WARNINGs (writer-emitted bloat)."""
        if self.n_hard_errors:
            return 1
        if strict and self.n_warnings:
            return 1
        return 0


# ---------------------------------------------------------------------------
# Validation passes (backend-driven, Flask-free)
# ---------------------------------------------------------------------------


def validate_reciters(backend, bucket_id: str, slugs: list[str]) -> list[ReciterReport]:
    """Run the per-reciter artefact audit for each slug."""
    from services.storage.bucket_audit import audit

    reports: list[ReciterReport] = []
    for slug in slugs:
        try:
            result = audit(backend, bucket_id, slug)
        except Exception as e:  # noqa: BLE001 — one bad folder must not abort the whole sweep
            # e.g. a non-UTF8 edit_history.jsonl raises UnicodeDecodeError out
            # of audit(); record it as a hard error for THIS slug and keep
            # going so the remaining reciters are still reported.
            reports.append(
                ReciterReport(
                    slug=slug,
                    found=True,
                    n_errors=1,
                    n_missing=0,
                    n_legacy=0,
                    n_warnings=0,
                    error_details=[f"audit raised {type(e).__name__}: {e}"],
                )
            )
            continue
        errs = [f"{f.path}: {f.detail}" for f in result.files if f.status == "error"]
        reports.append(
            ReciterReport(
                slug=slug,
                found=result.found,
                n_errors=result.n_errors,
                n_missing=result.n_missing,
                n_legacy=result.n_legacy,
                n_warnings=result.n_warnings,
                error_details=errs,
            )
        )
    return reports


def validate_sidecars(backend) -> list[SidecarReport]:
    """Validate every ``catalog/audio_manifest/<slug>.json`` sidecar.

    Reads each sidecar through the backend and parses it via the canonical
    ``AudioManifestSidecar`` model (``_meta`` aliasing handled by the model).
    The dead ``catalog/reciter_catalog.json`` backup is never touched.
    """
    from qua_shared.schemas import AudioManifestSidecar

    try:
        names = backend.list_dir(_AUDIO_MANIFEST_PREFIX)
    except Exception as e:  # noqa: BLE001
        log.warning("could not list %s: %s", _AUDIO_MANIFEST_PREFIX, e)
        names = []

    reports: list[SidecarReport] = []
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        slug = name[: -len(".json")]
        path = f"{_AUDIO_MANIFEST_PREFIX}/{name}"
        try:
            raw = backend.read_bytes(path)
        except Exception as e:  # noqa: BLE001
            reports.append(SidecarReport(slug=slug, ok=False, detail=f"read fail: {e}"))
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as e:
            reports.append(SidecarReport(slug=slug, ok=False, detail=f"invalid JSON: {e}"))
            continue
        try:
            AudioManifestSidecar.model_validate(doc)
        except Exception as e:  # noqa: BLE001 — pydantic ValidationError + guards
            reports.append(SidecarReport(slug=slug, ok=False, detail=f"schema fail: {e}"))
            continue
        reports.append(SidecarReport(slug=slug, ok=True))
    return reports


def validate_catalog(*, pull: bool = False) -> tuple[bool, str]:
    """Rebuild + validate the DB ``ReciterCatalog`` via ``snapshot()``.

    With ``pull=True`` (the nightly CLI path) it first pulls ``db/inspector.db``
    from the configured bucket into the isolated ``INSPECTOR_DB_PATH`` (pinned
    by ``_setup_paths_and_env``) and runs migrations, mirroring app boot —
    there is no pre-pulled ``inspector.db`` in CI. With ``pull=False`` (the
    default; library + test callers seed their own DB) it snapshots whatever
    DB is at the configured path. Returns ``(ok, detail)``. A pydantic
    ``ValidationError`` on any DB row, a bucket with no DB (when pulling), or
    an open/query failure is a hard error.
    """
    from pydantic import ValidationError

    from services.db import init_db, repo_catalog
    from services.db import sync as db_sync

    try:
        if pull:
            if not db_sync.pull():
                return False, "no db/inspector.db in bucket"
            init_db()  # open writer + run migrations (fail-fast), as at boot
        catalog = repo_catalog.snapshot()
    except ValidationError as e:
        return False, f"catalog schema fail: {e}"
    except Exception as e:  # noqa: BLE001 — DB pull / open / query failure
        return False, f"catalog snapshot failed: {type(e).__name__}: {e}"
    return True, f"{len(catalog.reciters)} reciters, {len(catalog.deliveries)} deliveries"


def validate_bucket(
    backend, bucket_id: str, *, check_catalog: bool = True, pull_db: bool = False
) -> BucketReport:
    """Run all three validation passes against ``backend`` and roll up.

    ``backend`` is any object exposing ``read_bytes(path) -> bytes`` (raising
    on missing) and ``list_dir(prefix) -> list[str]`` — the bucket backend in
    production, a ``FilesystemBackend`` in tests. ``check_catalog`` validates
    the DB catalog (needs a local DB); tests that only exercise the bucket
    files pass ``False``. ``pull_db`` (CLI only) pulls + migrates the bucket DB
    first; library/test callers seed their own DB and leave it ``False``.
    """
    from services.storage import data_dir

    slugs = sorted(Path(p).name for p in data_dir.list_slugs())
    report = BucketReport(bucket_id=bucket_id)
    report.reciters = validate_reciters(backend, bucket_id, slugs)
    report.sidecars = validate_sidecars(backend)
    if check_catalog:
        report.catalog_ok, report.catalog_detail = validate_catalog(pull=pull_db)
    else:
        report.catalog_checked = False
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(report: BucketReport) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"Whole-bucket validation: {report.bucket_id}")
    lines.append("=" * 72)

    # Reciters
    n_found = sum(1 for r in report.reciters if r.found)
    lines.append(f"Reciters: {len(report.reciters)} slugs ({n_found} found)")
    for r in report.reciters:
        if r.n_errors:
            lines.append(f"  [FAIL] {r.slug}: {r.n_errors} error(s)")
            for d in r.error_details:
                lines.append(f"           {d}")
        elif r.n_warnings:
            lines.append(f"  [WARN] {r.slug}: {r.n_warnings} unknown field(s)")
        elif r.n_legacy:
            lines.append(f"  [INFO] {r.slug}: {r.n_legacy} legacy strip(s)")
    lines.append(
        f"  -> {report.n_reciter_errors} error(s)  |  "
        f"{report.n_warnings} unknown-field warning(s)  |  "
        f"{report.n_legacy} legacy strip(s)"
    )

    # Sidecars
    lines.append("-" * 72)
    lines.append(f"Audio-manifest sidecars: {len(report.sidecars)}")
    for s in report.sidecars:
        if not s.ok:
            lines.append(f"  [FAIL] {s.slug}: {s.detail}")
    lines.append(f"  -> {report.n_sidecar_errors} error(s)")

    # Catalog
    lines.append("-" * 72)
    if not report.catalog_checked:
        lines.append("DB catalog: skipped")
    elif report.catalog_ok:
        lines.append(f"DB catalog: OK ({report.catalog_detail})")
    else:
        lines.append(f"DB catalog: FAIL — {report.catalog_detail}")

    # Grand total
    lines.append("=" * 72)
    lines.append(
        f"TOTAL: {report.n_hard_errors} hard error(s)  |  "
        f"{report.n_warnings} unknown-field warning(s)  |  "
        f"{report.n_legacy} legacy strip(s)"
    )
    return "\n".join(lines)


def _to_json(report: BucketReport, *, strict: bool) -> dict:
    return {
        "bucket": report.bucket_id,
        "reciters": [
            {
                "slug": r.slug,
                "found": r.found,
                "n_errors": r.n_errors,
                "n_missing": r.n_missing,
                "n_legacy": r.n_legacy,
                "n_warnings": r.n_warnings,
                "errors": r.error_details,
            }
            for r in report.reciters
        ],
        "sidecars": [{"slug": s.slug, "ok": s.ok, "detail": s.detail} for s in report.sidecars],
        "catalog": {
            "checked": report.catalog_checked,
            "ok": report.catalog_ok,
            "detail": report.catalog_detail,
        },
        "totals": {
            "hard_errors": report.n_hard_errors,
            "reciter_errors": report.n_reciter_errors,
            "sidecar_errors": report.n_sidecar_errors,
            "unknown_field_warnings": report.n_warnings,
            "legacy_strips": report.n_legacy,
        },
        "exit_code": report.exit_code(strict=strict),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--bucket",
        choices=sorted(_BUCKETS),
        default="dev",
        help="Bucket to validate (default: dev). Prod needs INSPECTOR_ALLOW_PROD_BUCKET=1.",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Also exit non-zero when any unknown field (WARNING) is seen.",
    )
    ap.add_argument(
        "--no-catalog",
        action="store_true",
        help="Skip the DB-catalog pass (e.g. when no local inspector.db is present).",
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit JSON instead of the human-readable summary."
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _setup_paths_and_env(args.bucket)

    from services.storage.hf_bucket import get_backend

    backend = get_backend()
    bucket_id = _BUCKETS[args.bucket]
    report = validate_bucket(
        backend, bucket_id, check_catalog=not args.no_catalog, pull_db=not args.no_catalog
    )

    if args.json:
        print(json.dumps(_to_json(report, strict=args.strict), indent=2, ensure_ascii=False))
    else:
        print(_render(report))

    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
