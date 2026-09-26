#!/usr/bin/env python3
"""HF dataset catalog refresh entrypoint — the cheap ``mushafs`` remediation.

Rebuilds ONLY the dataset catalog config (``mushafs``) + the dataset card from
the live Inspector SQLite catalog and re-pushes them. No audio slicing, no
per-verse parquet rebuild — this is the targeted fix for ``catalog_edit``
staleness (a metadata change that desynced the published catalog from
canonical). Reuses ``publish_hf._sync_dataset_catalog_and_card`` verbatim so the
projection logic can't drift from the per-recitation publish path.

On completion it POSTs ``/api/webhooks/hf-catalog-refresh-complete`` so the
Inspector (single DB writer) clears the now-reconciled ``catalog_edit`` HF
staleness. The 120 s poll fallback covers a missed webhook.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from qua_shared.inspector_notify import WEBHOOK_USER_AGENT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("refresh_hf_catalog")


def _post_webhook(*, job_id: str, status: str) -> bool:
    url = os.environ.get("INSPECTOR_WEBHOOK_URL", "").strip()
    secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        log.info("webhook URL/secret unset — skipping callback (poll fallback applies)")
        return False
    import urllib.request

    data = json.dumps({"kind": "refresh_catalog", "job_id": job_id, "status": status}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Inspector-Job-Secret": secret,
            "User-Agent": WEBHOOK_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("webhook %s → %s", url, resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("webhook POST failed: %s", exc)
        return False


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip() or "unknown"
    if not os.environ.get("HF_TOKEN", "").strip():
        log.error("HF_TOKEN secret is required")
        return 10
    from qua_jobs.publish_hf import _resolve_dataset_repo_id, _sync_dataset_catalog_and_card

    repo_id = _resolve_dataset_repo_id()
    log.info("refresh_hf_catalog: repo=%s job=%s", repo_id, job_id)
    try:
        _sync_dataset_catalog_and_card(repo_id)
    except Exception as exc:
        log.error("catalog refresh failed: %s", exc)
        _post_webhook(job_id=job_id, status="failed")
        return 1
    _post_webhook(job_id=job_id, status="succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
