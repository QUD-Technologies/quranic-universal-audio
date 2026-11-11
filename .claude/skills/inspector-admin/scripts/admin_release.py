"""GH release cut helper.

  admin_release.py preview [--prod]
  admin_release.py cut [--version vX.Y.Z] [--monitor] --prod --yes-prod
  admin_release.py monitor JOB_ID

Uses the same preview service as ``GET /api/admin/release-preview`` and the
same launch service as ``POST /api/admin/cut-release``. Prod cuts should use a
webhook secret so Inspector can record the release ledger when the HF job
finishes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _bootstrap as bs  # noqa: E402

PROD_WEBHOOK_BASE = "https://qud-technologies-quranic-universal-audio.hf.space"
TERMINAL = {
    "succeeded",
    "completed",
    "failed",
    "error",
    "errored",
    "timed-out",
    "timeout",
    "stopped",
    "canceled",
    "cancelled",
    "deleted",
}


def _print(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _preview():
    from services.admin.release_preview import build_release_preview  # noqa: E402

    return build_release_preview()


def _print_preview(payload, *, body: bool = True) -> None:
    counts = payload.change_counts
    print(f"computed_version: {payload.computed_version or '<manual required>'}")
    print(f"previous_version: {payload.previous_version or '<none>'}")
    print(f"changes: added={counts.added} refresh={counts.refresh} unchanged={counts.unchanged}")
    print(f"release_date: {payload.release_date}")
    print(f"expected_version_at_preview: {payload.expected_version_at_preview or '<none>'}")
    if body:
        print()
        print(payload.changelog_preview_md)


def _ensure_launch_secrets(*, allow_no_webhook: bool) -> None:
    if not os.environ.get("INSPECTOR_GH_RELEASE_TOKEN") and os.environ.get("GITHUB_TOKEN"):
        os.environ["INSPECTOR_GH_RELEASE_TOKEN"] = os.environ["GITHUB_TOKEN"]
    if not os.environ.get("INSPECTOR_GH_RELEASE_TOKEN"):
        raise RuntimeError("missing INSPECTOR_GH_RELEASE_TOKEN or GITHUB_TOKEN")
    if not os.environ.get("INSPECTOR_WEBHOOK_SECRET") and not allow_no_webhook:
        raise RuntimeError(
            "missing INSPECTOR_WEBHOOK_SECRET; refusing to launch a cut that cannot "
            "record the prod Inspector release ledger"
        )


def _do_preview(a, ctx) -> int:
    _print_preview(_preview(), body=not a.summary_only)
    return 0


def _do_cut(a, ctx) -> int:
    from services.admin.jobs import base as jobs_base  # noqa: E402
    from services.admin.jobs import cut_release as cut_release_jobs  # noqa: E402

    payload = _preview()
    _print_preview(payload, body=not a.summary_only)
    print()

    if sum(payload.change_counts.model_dump().values()) == 0:
        print("no eligible recitations", file=sys.stderr)
        return 4
    version = (a.version or "").strip() or None
    if payload.needs_manual_version and version is None:
        print("manual --version required because preview computed no version", file=sys.stderr)
        return 5
    if jobs_base.running_job_for(kind="cut_release") is not None:
        print("a cut_release job is already running", file=sys.stderr)
        return 6
    if a.dry_run:
        _print("DRY RUN - would launch cut_release")
        return 0

    _ensure_launch_secrets(allow_no_webhook=a.allow_no_webhook)
    launched_by = os.environ.get("INSPECTOR_DEV_OWNER_HF_ID", "").strip() or None
    result = cut_release_jobs.launch(
        version=version,
        launched_by=launched_by,
        webhook_base=PROD_WEBHOOK_BASE if a.prod else None,
    )
    _print(f"launched job_id={result.get('job_id')}")
    _print(f"  URL: {result.get('url')}")
    if a.allow_no_webhook and not os.environ.get("INSPECTOR_WEBHOOK_SECRET"):
        _print("WARNING: no webhook secret; GitHub release may cut without Inspector ledger update")
    bs.after_write_banner(a)
    if a.monitor:
        rc = _do_monitor_loop(result["job_id"], poll_secs=a.poll_secs)
        if rc == 0 and a.allow_no_webhook and not os.environ.get("INSPECTOR_WEBHOOK_SECRET"):
            final_version = version or payload.computed_version
            if final_version:
                _complete_from_gh(final_version, result["job_id"], launched_by=launched_by)
                bs.after_write_banner(a)
        return rc
    return 0


def _do_complete(a, ctx) -> int:
    launched_by = a.launched_by or os.environ.get("INSPECTOR_DEV_OWNER_HF_ID", "").strip() or None
    _complete_from_gh(a.version, a.job_id, launched_by=launched_by)
    bs.after_write_banner(a)
    return 0


def _do_monitor(a, ctx) -> int:
    return _do_monitor_loop(a.job_id, poll_secs=a.poll_secs)


def _do_monitor_loop(job_id: str, *, poll_secs: int = 15) -> int:
    from huggingface_hub import fetch_job_logs, inspect_job  # noqa: E402

    seen = 0
    last_status = None
    while True:
        try:
            info = inspect_job(job_id=job_id)
        except Exception as exc:
            _print(f"inspect_job error: {exc}")
            time.sleep(poll_secs)
            continue
        status = (getattr(getattr(info, "status", None), "stage", "") or "").lower()
        if status != last_status:
            _print(f"status: {status}")
            last_status = status
        try:
            lines = list(fetch_job_logs(job_id=job_id))
        except Exception:
            lines = []
        for line in lines[seen:]:
            print(f"   {line}", flush=True)
        seen = len(lines)
        if status in TERMINAL:
            _print(f"terminal: {status}")
            return 0 if status in {"succeeded", "completed"} else 1
        time.sleep(poll_secs)


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _complete_from_gh(version: str, job_id: str, *, launched_by: str | None) -> None:
    from qua_shared.config_loader import repo_config  # noqa: E402
    from services.admin.jobs import cut_release as cut_release_jobs  # noqa: E402

    cfg = repo_config()
    owner = cfg.get("repo_owner", "")
    repo = cfg.get("repo_name", "")
    if not owner or not repo:
        raise RuntimeError("repo_owner/repo_name missing from repo config")
    base = f"https://github.com/{owner}/{repo}/releases/download/{version}"
    manifest = _fetch_json(f"{base}/manifest.json")
    catalog = _fetch_json(f"{base}/catalog.json")
    catalog_by_slug = {row["slug"]: row for row in catalog.get("recitations", [])}
    members = []
    for slug, rec in manifest.get("recitations", {}).items():
        members.append(
            {
                "slug": slug,
                "catalog_snapshot": catalog_by_slug[slug],
                "zip_sha256": rec["sha256"],
                "zip_bytes": int(rec["bytes"]),
                "coverage_ayahs": int(rec["coverage_ayahs"]),
                "content_hash": rec.get("content_hash"),
                "ts_version": rec["ts_version"],
                "change_kind": rec["change_kind"],
            }
        )
    external_uri = f"https://github.com/{owner}/{repo}/releases/tag/{version}"
    result = cut_release_jobs.complete(
        None,
        job_id,
        version=version,
        external_uri=external_uri,
        launched_by=launched_by,
        members=members,
    )
    _print(f"recorded Inspector release ledger: {result}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("preview", help="render the current release preview")
    pp.add_argument("--summary-only", action="store_true")
    bs.add_common_args(pp, mutating=False)

    pc = sub.add_parser("cut", help="launch the global GH release cut job")
    pc.add_argument("--version", help="manual vX.Y.Z override")
    pc.add_argument("--monitor", action="store_true")
    pc.add_argument("--poll-secs", type=int, default=15)
    pc.add_argument("--summary-only", action="store_true")
    pc.add_argument(
        "--allow-no-webhook",
        action="store_true",
        help="allow a launch without webhook; with --monitor, record the ledger from GH assets",
    )
    bs.add_common_args(pc, mutating=True)

    pcomp = sub.add_parser("complete", help="record a finished GH release from its assets")
    pcomp.add_argument("version")
    pcomp.add_argument("--job-id", required=True)
    pcomp.add_argument("--launched-by")
    bs.add_common_args(pcomp, mutating=True)

    pm = sub.add_parser("monitor", help="stream logs for an existing cut job")
    pm.add_argument("job_id")
    pm.add_argument("--poll-secs", type=int, default=15)
    bs.add_common_args(pm, mutating=False)

    a = p.parse_args()
    need_db = a.cmd in {"preview", "cut", "complete"}
    # cut + complete mutate (--yes-prod). Only `complete` does an in-process DB
    # write (records the gh_releases ledger) → safe_write; `cut` only launches
    # the HF job (the ledger lands later via webhook / `complete`).
    mutates = a.cmd in ("cut", "complete")
    safe_write = a.cmd == "complete"
    return bs.run(
        a,
        lambda ctx: {
            "preview": _do_preview,
            "cut": _do_cut,
            "complete": _do_complete,
            "monitor": _do_monitor,
        }[a.cmd](a, ctx),
        need_actor=False, mutates=mutates, safe_write=safe_write, need_db=need_db,
    )


if __name__ == "__main__":
    sys.exit(main())
