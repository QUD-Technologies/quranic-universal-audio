"""Shared release-preview builder for the Releases tab and admin CLI."""

from __future__ import annotations

from datetime import UTC, datetime

from qua_shared.config_loader import repo_config
from qua_shared.dataset_license import DATA_LICENSE_ID
from qua_shared.release_changelog import render_changelog
from qua_shared.schemas import AdminReleasePreviewResponse
from services.db import get_conn, repo_releases


def _tiers_for(riwayah_slug: str | None) -> list[str]:
    """The timing depths a delivery in this riwayah can ship.

    Only the reference edition is timed natively; every other one is aligned
    against Hafs proxy phones and keeps word boundaries alone, so the preview
    must not promise a letter tier the cut will not emit.
    """
    from qua_shared.riwayat import is_hafs

    return ["verse", "word", "letter"] if is_hafs(riwayah_slug) else ["verse", "word"]


def build_release_preview() -> AdminReleasePreviewResponse:
    """Compute the dry-run GH release preview without building assets."""
    conn = get_conn()
    candidates = conn.execute("""
        SELECT prr.slug AS slug,
               prr.version AS ts_version,
               r.name_en AS name_en,
               r.name_ar AS name_ar,
               rw.name AS riwayah,
               d.riwayah AS riwayah_slug,
               st.name AS style,
               c.name AS channel,
               d.chapter_count AS coverage_surahs
        FROM per_recitation_releases prr
        JOIN deliveries d ON d.slug = prr.slug
        JOIN channels c   ON c.slug = d.channel
        JOIN riwayahs rw  ON rw.slug = d.riwayah
        JOIN styles st    ON st.slug = d.style
        JOIN reciters r   ON r.reciter_id = d.reciter_id
        WHERE prr.track = 'ts'
          AND prr.superseded_at IS NULL
          AND d.channel <> 'everyayah'
        ORDER BY prr.slug
    """).fetchall()

    prior = repo_releases.latest_gh_release()
    prior_members = (
        {m["slug"]: m for m in repo_releases.gh_release_recitations(prior["id"])} if prior else {}
    )

    added: list[dict] = []
    refreshed: list[dict] = []
    unchanged: list[dict] = []
    for row in candidates:
        slug = row["slug"]
        ts_version = str(row["ts_version"])
        prior_member = prior_members.get(slug)
        row_payload = {
            "slug": slug,
            "name_en": row["name_en"],
            "name_ar": row["name_ar"],
            "riwayah": row["riwayah"],
            "style": row["style"],
            "channel": row["channel"],
            "coverage_surahs": row["coverage_surahs"],
            "ts_version": ts_version,
            "tiers": _tiers_for(row["riwayah_slug"]),
        }
        if prior_member is None:
            row_payload["change_kind"] = "added"
            added.append(row_payload)
        elif str(prior_member["ts_version"]) != ts_version:
            row_payload["change_kind"] = "refresh"
            refreshed.append(row_payload)
        else:
            row_payload["change_kind"] = "unchanged"
            unchanged.append(row_payload)

    prior_version = prior["version"] if prior else None
    if added:
        computed_version = _bump_minor(prior_version)
    elif refreshed:
        computed_version = _bump_patch(prior_version)
    else:
        computed_version = None

    cfg = repo_config()
    owner, repo, hf_dataset = (
        cfg.get("repo_owner", ""),
        cfg.get("repo_name", ""),
        cfg.get("hf_dataset", ""),
    )
    release_date = datetime.now(UTC).strftime("%d-%m-%Y")
    preview_members = [{**m, "coverage_ayahs": None} for m in (added + refreshed + unchanged)]

    return AdminReleasePreviewResponse.model_validate(
        {
            "computed_version": computed_version,
            "needs_manual_version": computed_version is None,
            "previous_version": prior_version,
            "change_counts": {
                "added": len(added),
                "refresh": len(refreshed),
                "unchanged": len(unchanged),
            },
            "added": added,
            "refreshed": refreshed,
            "release_date": release_date,
            "license": DATA_LICENSE_ID,
            "links": {
                "repo": f"https://github.com/{owner}/{repo}" if owner and repo else "",
                "hf_dataset": (
                    f"https://huggingface.co/datasets/{hf_dataset}" if hf_dataset else ""
                ),
            },
            "changelog_preview_md": render_changelog(
                version=computed_version or "v?.?.?",
                previous_version=prior_version,
                release_date=release_date,
                members=preview_members,
                owner=owner,
                repo=repo,
                hf_dataset=hf_dataset,
            ),
            "expected_version_at_preview": computed_version,
        }
    )


def current_auto_version() -> tuple[str | None, int]:
    """Return the preview-equivalent auto version and changed member count."""
    preview = build_release_preview()
    counts = preview.change_counts
    changed = counts.added + counts.refresh
    eligible = changed + counts.unchanged
    if changed == 0 and eligible and preview.computed_version is not None:
        changed = 1  # the public schema/DigitalKhatt asset migration itself
    return preview.computed_version, changed


def _bump_minor(prior: str | None) -> str:
    if not prior:
        return "v0.1.0"
    parts = prior.lstrip("v").split(".")
    major, minor = int(parts[0]), int(parts[1])
    return f"v{major}.{minor + 1}.0"


def _bump_patch(prior: str | None) -> str:
    if not prior:
        return "v0.1.0"
    parts = prior.lstrip("v").split(".")
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return f"v{major}.{minor}.{patch + 1}"
