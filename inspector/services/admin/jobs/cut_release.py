"""GH release cut kind — global dataset snapshot.

Launches an HF Job that runs ``qua_jobs/cut_release.py``. The job:

  1. Reads the bucket catalog to discover every recitation whose channel is
     ``gh_release_eligible`` AND has a current ``per_recitation_releases(track='ts')``.
  2. Builds per-recitation tier files (verse / word / letter — top-down
     projection) + catalog.json + per-recitation manifest.json.
  3. Packs each recitation into a ``<slug>.zip`` (store-only for `.gz` entries,
     deflate-9 for JSON; deterministic mtime=0).
  4. Computes ``content_hash`` = SHA-256(letter_tier.json.gz_bytes ||
     catalog.json_bytes) per recitation.
  5. Builds the dataset-level ``manifest.json`` + ``CHANGELOG.md`` (diff
     against the previous gh_release).
  6. Creates the GH release tag via the GH API (fine-grained app token) and
     uploads every asset.

On webhook completion (with ``version`` in the payload), ``complete()`` here
inserts a ``gh_releases`` row + N ``gh_release_recitations`` rows and fires
``released({track:'gh', version, recitation_count})``.

Global single-flight: only one cut at a time across the system.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from qua_shared.schemas import Actor, Role
from services.db import repo_releases
from services.db.sync import durable_transaction
from services.state import audit
from services.storage.hf_bucket import resolve_bucket_repo

from . import base, records

log = logging.getLogger("inspector")

KIND = "cut_release"

JOB_FLAVOR = os.environ.get("INSPECTOR_CUT_JOB_FLAVOR", "cpu-upgrade")
JOB_TIMEOUT = os.environ.get("INSPECTOR_CUT_JOB_TIMEOUT", "2h")


def launch(
    *,
    version: str | None = None,
    launched_by: str | None = None,
    settings=None,
    webhook_base: str | None = None,
) -> dict:
    """Launch a cut-release job. Returns ``{job_id, url}``.

    Global single-flight — refuses if another cut is in flight. ``settings`` is
    an optional ``ReleaseSettings`` (clip-edge pads); its fields thread to the
    job as ``PUBLISH_PAD_START`` / ``PUBLISH_PAD_END`` / ``PUBLISH_MIN_GAP`` —
    the SAME env the HF publish reads, so the release verse bounds match the
    dataset's clip windows.
    """
    from huggingface_hub import Volume, get_token, run_job

    busy = base.running_job_for(kind=KIND)
    if busy is not None:
        raise RuntimeError(f"cut_release already in flight: kind={busy[0]} id={busy[1]}")

    base.stage_job_code()
    bucket = resolve_bucket_repo()
    env = {
        "INSPECTOR_BUCKET_MOUNT": "/data",
        "PYTHONPATH": "/aux/code",
    }
    if version:
        env["RELEASE_VERSION"] = version
    if launched_by:
        env["LAUNCHED_BY"] = launched_by
    if settings is not None:
        env["PUBLISH_PAD_START"] = str(settings.pad_start)
        env["PUBLISH_PAD_END"] = str(settings.pad_end)
        env["PUBLISH_MIN_GAP"] = str(settings.min_gap)
    secrets = {"HF_TOKEN": get_token()}
    gh_token = os.environ.get("INSPECTOR_GH_RELEASE_TOKEN", "").strip()
    if gh_token:
        secrets["GH_RELEASE_TOKEN"] = gh_token
    webhook_secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if webhook_secret and webhook_base:
        env["INSPECTOR_WEBHOOK_URL"] = (
            webhook_base.rstrip("/") + "/api/webhooks/release-cut-complete"
        )
        secrets["INSPECTOR_WEBHOOK_SECRET"] = webhook_secret

    # cut_release.py is stdlib-only at runtime (urllib for GH API, sqlite3 for
    # DB reads) but pulls pyyaml + huggingface_hub via
    # ``qua_shared.config_loader.repo_config``. Install on every launch — pip
    # is a no-op when wheels are cached.
    deps = "pyyaml huggingface_hub"
    entrypoint = "python /aux/code/qua_jobs/cut_release.py"
    command = base.job_command(entrypoint, deps)

    job = run_job(
        image=base.JOB_IMAGE,
        command=command,
        flavor=JOB_FLAVOR,  # type: ignore[reportArgumentType]  # HF accepts the str value of SpaceHardware
        timeout=JOB_TIMEOUT,
        env=env,
        secrets=secrets,
        volumes=[
            Volume(type="bucket", source=bucket, mount_path="/data"),
            Volume(type="bucket", source=base.ALIGNER_BUCKET, mount_path="/aux", read_only=True),
        ],
        labels={"task": KIND, "reciter": "_global"},
    )
    job_id = base.hf_job_id(job) or ""
    url = getattr(job, "url", None)
    records.record_launch(KIND, None, job_id, url=url, launched_by=launched_by)
    log.info("launched cut_release job %s (version=%s)", job_id, version)
    # Same rationale as hf_publish.launch — drop the in-flight cache so the
    # next /releases/status reflects the new job without TTL latency.
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()
    return {"job_id": job_id, "url": url}


def complete(
    slug: str | None,
    job_id: str,
    *,
    version: str | None = None,
    external_uri: str | None = None,
    launched_by: str | None = None,
    members: list[dict] | None = None,
    validation_summary: dict | None = None,
) -> dict:
    """Record a GH release in the DB. Idempotent on (version).

    Inserts ``gh_releases`` + N ``gh_release_recitations`` rows, supersedes
    prior gh_releases, fires ``released({track:'gh', version, ...})``.

    ``members`` is the per-recitation membership list from the job: each
    entry has ``slug, catalog_snapshot, zip_sha256, zip_bytes, coverage_ayahs,
    content_hash, ts_version, change_kind``.
    """
    if version is None:
        log.warning("cut_release.complete called without version")
        return {"ok": False, "reason": "no version"}
    members = members or []
    now = datetime.now(UTC)
    actor = Actor(
        hf_user_id="SYSTEM_ACTOR",
        login_at_time=launched_by or "system",
        role=Role.OWNER,
    )
    with durable_transaction() as _:
        # Idempotency: any row (current OR superseded) with this version → no-op.
        # ``latest_gh_release`` only sees the current row; a retry after a
        # subsequent cut would miss its prior self.
        existing_any = repo_releases.gh_release_by_version(version)
        if existing_any is not None:
            log.info(
                "cut_release.complete(%s): already recorded (id=%s)",
                version,
                existing_any.get("id"),
            )
            return {"ok": True, "skipped": "duplicate", "release_id": existing_any["id"]}
        # Supersede prior current gh_releases FIRST — the partial-unique on
        # ``version WHERE superseded_at IS NULL`` allows multiple rows only if
        # they're all superseded; the at-most-one-current invariant requires
        # the prior current row to be marked before INSERTing the new one.
        repo_releases.supersede_prior_gh_releases(except_id=-1, at=now)
        release_id = repo_releases.insert_gh_release(
            version=version,
            produced_at=now,
            produced_by="SYSTEM_ACTOR",
            produced_by_job_id=job_id,
            launched_by=launched_by,
            external_uri=external_uri,
            validation_summary=validation_summary,
        )
        for m in members:
            repo_releases.insert_gh_release_recitation(
                release_id=release_id,
                slug=m["slug"],
                catalog_snapshot=m["catalog_snapshot"],
                zip_sha256=m["zip_sha256"],
                zip_bytes=int(m["zip_bytes"]),
                coverage_ayahs=int(m["coverage_ayahs"]),
                content_hash=m.get("content_hash"),
                ts_version=m["ts_version"],
                change_kind=m["change_kind"],
            )
        audit.append(
            "released",
            actor=actor,
            slug=None,
            payload={
                "track": "gh",
                "version": version,
                "recitation_count": len(members),
                "job_id": job_id,
            },
            reason="cut_release",
        )
    slim_members = [
        {
            "slug": m.get("slug"),
            "status": "succeeded",
            "version": m.get("ts_version"),
            "change_kind": m.get("change_kind"),
        }
        for m in members
        if m.get("slug")
    ]
    records.record_terminal(
        KIND,
        None,
        job_id,
        status="succeeded",
        version=version,
        external_uri=external_uri,
        members=slim_members,
        validation_summary=validation_summary,
        launched_by=launched_by,
    )
    log.info("cut_release.complete(%s): recorded %d recitations", version, len(members))
    # Terminal transition — drop the in-flight cache so the FE removes the
    # cut row from "In progress" on the next fetch.
    from services.storage import cache as _cache

    _cache.invalidate_in_flight_jobs_cache()

    # Email subscribers opted into release notifications (best-effort). Past the
    # idempotency guard above, so a webhook retry can't double-send.
    try:
        from services.email import emit as _email

        _email.emit_github_release(version=version)
    except Exception:  # noqa: BLE001
        log.exception("email github_release hook failed (version=%s)", version)

    return {"ok": True, "release_id": release_id, "recitation_count": len(members)}


def register() -> None:
    # The poll fallback only triggers for jobs whose ``complete()`` can be
    # called with just (slug, job_id). cut_release.complete() needs the full
    # membership payload, which only the webhook + job-side cooperation
    # provides — so don't register for poll dispatch; webhook is mandatory.
    return
