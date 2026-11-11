"""Count report — one reciter's validation counts, straight from the engine.

Prints the 13 ``category_counts`` and the ``stats`` block that
``services.validation.validate_reciter_segments`` returns for a reciter. Two
runs of this script (a baseline arm and a candidate arm) are what a segmenter
A/B is scored on. Every number is the engine's own, so the two arms cannot
drift apart through a second implementation.

The one exception is ``low_confidence``. ``category_counts`` reports it as the
length of the detail list, built against ``LOW_CONFIDENCE_DETAIL_THRESHOLD =
1.0`` — every segment that is not a perfect match, typically 40% of a reciter.
The accordion badge and the mark-ready gate both count the strict
``LOW_CONFIDENCE_THRESHOLD = 0.80`` band instead, so this report does too: an
A/B has to move the number a reviewer acts on. The ``<1.0`` tier stays visible
on its own line rather than disappearing.

**One slug, one bucket, one process.** ``get_backend()`` is a process-wide
singleton whose bucket id is fixed at construction, so a prod baseline against
a dev candidate is two invocations::

    python scripts/diagnostics/count_report.py <slug> --bucket prod
    python scripts/diagnostics/count_report.py <slug>-bnd --bucket dev

Read-only, so ``INSPECTOR_ALLOW_PROD_BUCKET=1`` is set automatically for
``--bucket prod``. Needs ``HF_TOKEN`` (env or ``.env``). Run from the repo root.
Exits 2 when the reciter has no saved segments on that bucket — never a silent
comparison against nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

# A reciter absent from the bucket is a caller mistake to fix, not a report.
EXIT_NO_SEGMENTS = 2

_LABEL_WIDTH = 20


def _setup_paths_and_env(bucket: str) -> None:
    """Insert repo root + inspector/ on sys.path; pin the bucket; load ``.env``.

    ``resolve_bucket_repo`` refuses prod from a non-deployed process without an
    explicit acknowledgement; this report only ever reads, so it sets the
    acknowledgement itself for ``--bucket prod``.
    """
    repo = Path(__file__).resolve().parents[2]
    inspector = repo / "inspector"
    if not inspector.is_dir():
        raise SystemExit(f"inspector/ not found at {inspector}")
    sys.path.insert(0, str(inspector))
    sys.path.insert(0, str(repo))

    env_path = repo / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    # Force the bucket backend even when ``.env`` pins ``INSPECTOR_BACKEND=
    # filesystem`` for local fixture dev — this report reads real bucket data.
    os.environ["INSPECTOR_BACKEND"] = "bucket"
    os.environ["INSPECTOR_BUCKET_REPO"] = _BUCKETS[bucket]
    if bucket == "prod":
        os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"


def _fmt(value: object) -> str:
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _strict_counts(result: dict) -> tuple[dict, int]:
    """``category_counts`` with ``low_confidence`` at 0.80, plus the ``<1.0`` total.

    Mirrors what ``state.mark_ready`` does to the same field, for the same reason.
    """
    from config import LOW_CONFIDENCE_THRESHOLD

    counts = dict(result["category_counts"])
    detail_total = counts.get("low_confidence", 0)
    items = result.get("low_confidence") or []
    counts["low_confidence"] = sum(
        1 for it in items if (it.get("confidence") or 0.0) < LOW_CONFIDENCE_THRESHOLD
    )
    return counts, detail_total


def _render(slug: str, bucket: str, result: dict) -> None:
    print(f"[{slug}]  bucket={bucket}")
    counts, detail_total = _strict_counts(result)
    print("\ncategory_counts")
    for name, count in counts.items():
        print(f"  {name:<{_LABEL_WIDTH}} {count:>10}")
    print(f"  {'(conf < 1.0)':<{_LABEL_WIDTH}} {detail_total:>10}")
    print("\nstats")
    stats = result.get("stats")
    if not stats:
        print("  (none — no verse data)")
        return
    for name, value in stats.items():
        print(f"  {name:<{_LABEL_WIDTH}} {_fmt(value):>10}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("slug", help="Reciter slug to report on.")
    ap.add_argument(
        "--bucket",
        choices=sorted(_BUCKETS),
        default="dev",
        help="Bucket to read (default: dev). Prod is read-only here.",
    )
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — already utf-8 or a non-reconfigurable stream
        pass

    _setup_paths_and_env(args.bucket)

    from services.validation import validate_reciter_segments

    result = validate_reciter_segments(args.slug)
    if result is None:
        print(
            f"{args.slug!r} has no saved segments on the {args.bucket} bucket — nothing to report.",
            file=sys.stderr,
        )
        return EXIT_NO_SEGMENTS
    _render(args.slug, args.bucket, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
