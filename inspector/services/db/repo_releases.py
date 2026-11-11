"""Repository for the v2 release tables.

Three tables (migration 0014):

  - ``per_recitation_releases`` — TS gen + HF dataset push history per slug.
    Partial-unique on ``(track, slug) WHERE superseded_at IS NULL`` enforces
    one current row per (track, slug).
  - ``gh_releases``             — global GH release snapshots, one row per cut.
  - ``gh_release_recitations``  — frozen membership of each GH release. Rows
    are immutable post-cut; ``stale_since`` set on TS regen for the
    most-recent release.

All writers run inside the caller's active ``durable_transaction`` so
supersede/stale stamping commits atomically with the new INSERT.

``mark_ts_refreshed`` is the out-of-band sibling of the HF-job completion path:
a manual backfill / re-stamp / local regen advances the current ``ts`` row's
``produced_at`` (clearing computed staleness) + re-stamps HF/GH stale + audits
``reciter.ts_refreshed``, so a bucket-direct upload is no longer silent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from qua_shared import release_staleness
from qua_shared.schemas import StaleReason

from . import _serde
from .connection import get_conn


def _require_rowid(rowid: int | None) -> int:
    """Return the rowid of a just-completed INSERT, which sqlite always sets."""
    if rowid is None:
        raise RuntimeError("INSERT did not produce a rowid")
    return rowid


# ---------------------------------------------------------------------------
# per_recitation_releases
# ---------------------------------------------------------------------------


def insert_per_recitation_release(
    *,
    track: str,
    slug: str,
    version: str,
    produced_at: datetime,
    produced_by: str,
    produced_by_job_id: str | None = None,
    launched_by: str | None = None,
    external_uri: str | None = None,
    source_catalog_rev: str | None = None,
    validation_summary: dict[str, Any] | None = None,
    affected_chapters: list[int] | None = None,
    prior_ts_version: str | None = None,
) -> int:
    """Insert a new release row. Caller must supersede the prior current row
    in the same transaction (see ``supersede_current``).

    ``affected_chapters`` (the chapters a ts regen folded in) and
    ``prior_ts_version`` (the superseded ts row's version) are regen provenance
    on the ``ts`` track — null on a first publish / the ``hf`` track.
    """
    cur = get_conn().execute(
        "INSERT INTO per_recitation_releases("
        " track, slug, version, produced_at, produced_by, produced_by_job_id,"
        " launched_by, external_uri, source_catalog_rev, validation_summary,"
        " affected_chapters, prior_ts_version"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            track,
            slug,
            version,
            _serde.to_iso(produced_at),
            produced_by,
            produced_by_job_id,
            launched_by,
            external_uri,
            source_catalog_rev,
            _serde.json_dumps(validation_summary) if validation_summary else None,
            _serde.json_dumps(affected_chapters) if affected_chapters else None,
            prior_ts_version,
        ),
    )
    return _require_rowid(cur.lastrowid)


def supersede_current(track: str, slug: str, *, except_id: int, at: datetime) -> int:
    """Mark prior current rows for (track, slug) as superseded, excluding the
    just-inserted row. Returns the number of rows updated.

    Same partial-unique pattern as ``ux_role_active`` / ``ux_claim_open_slug``.
    """
    cur = get_conn().execute(
        "UPDATE per_recitation_releases SET superseded_at = ? "
        "WHERE track = ? AND slug = ? AND id != ? AND superseded_at IS NULL",
        (_serde.to_iso(at), track, slug, except_id),
    )
    return int(cur.rowcount)


def current_release(track: str, slug: str) -> dict | None:
    """Return the current (non-superseded) row for ``(track, slug)`` or None."""
    r = (
        get_conn()
        .execute(
            "SELECT * FROM per_recitation_releases "
            "WHERE track = ? AND slug = ? AND superseded_at IS NULL",
            (track, slug),
        )
        .fetchone()
    )
    return dict(r) if r else None


def release_by_version(track: str, slug: str, version: str) -> dict | None:
    """Return ANY row (current OR superseded) for ``(track, slug, version)``.

    Idempotency guard for ``complete()`` handlers: a webhook retried after a
    newer publish would otherwise re-insert under a now-stale version. Using
    this lookup, the handler skips when the (track, slug, version) triple
    has already been recorded — superseded rows count.
    """
    r = (
        get_conn()
        .execute(
            "SELECT * FROM per_recitation_releases "
            "WHERE track = ? AND slug = ? AND version = ? "
            "ORDER BY id DESC LIMIT 1",
            (track, slug, version),
        )
        .fetchone()
    )
    return dict(r) if r else None


def all_releases_for_track_slug(track: str, slug: str) -> list[dict]:
    """Every row (current + superseded) for ``(track, slug)`` ordered by
    ``produced_at`` ascending — the full generation/publish history.

    Supersede stamps ``superseded_at`` and never deletes, so this reconstructs
    each TS generation (track='ts') or each publish (track='hf') as a boundary
    timeline. Feeds the Segments history "tier" view.
    """
    rows = (
        get_conn()
        .execute(
            "SELECT * FROM per_recitation_releases "
            "WHERE track = ? AND slug = ? ORDER BY produced_at ASC, id ASC",
            (track, slug),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def all_gh_releases() -> list[dict]:
    """Every gh_releases row (current + superseded) ordered by ``produced_at``
    ascending — the full cut history (cut markers for the history timeline)."""
    rows = (
        get_conn().execute("SELECT * FROM gh_releases ORDER BY produced_at ASC, id ASC").fetchall()
    )
    return [dict(r) for r in rows]


def gh_release_by_version(version: str) -> dict | None:
    """Return ANY gh_releases row (current OR superseded) for ``version``.

    Same idempotency rationale as ``release_by_version`` — a webhook retry
    after a subsequent cut shouldn't replay the row.
    """
    r = (
        get_conn()
        .execute(
            "SELECT * FROM gh_releases WHERE version = ? ORDER BY id DESC LIMIT 1",
            (version,),
        )
        .fetchone()
    )
    return dict(r) if r else None


def _reason_value(reason: StaleReason | str) -> str:
    return reason.value if isinstance(reason, StaleReason) else str(reason)


def _should_stamp(row: dict, incoming: StaleReason | str) -> bool:
    """Stamp a fresh row, or upgrade an already-stale one to a more severe
    reason (so a later ``ts_regen`` overrides an earlier ``catalog_edit`` — the
    catalog-refresh clear path depends on this ordering)."""
    if row.get("stale_since") is None:
        return True
    return release_staleness.more_severe(
        row.get("stale_reason"), StaleReason(_reason_value(incoming))
    )


def stamp_stale(
    slug: str,
    *,
    at: datetime,
    reason: StaleReason | str,
    tracks: tuple[str, ...] = ("hf", "gh"),
) -> int:
    """Mark the current ``hf`` row for ``slug`` (if any) and that slug's row in
    the most-recent ``gh_releases`` (if a member) stale with ``reason``. Returns
    the number of stamps applied (fresh or reason-upgrades).

    ``stale_since`` records *first* stale time (preserved on a reason upgrade
    via ``COALESCE``); ``stale_reason`` always reflects the most severe cause.
    ``tracks`` limits the stamp to the tracks the change reaches (a change only
    the GH cut ships passes ``("gh",)``). Runs inside the caller's
    ``durable_transaction``.
    """
    iso = _serde.to_iso(at)
    rv = _reason_value(reason)
    conn = get_conn()
    n = 0

    hf = conn.execute(
        "SELECT id, stale_since, stale_reason FROM per_recitation_releases "
        "WHERE track = 'hf' AND slug = ? AND superseded_at IS NULL",
        (slug,),
    ).fetchone()
    if "hf" in tracks and hf is not None and _should_stamp(dict(hf), reason):
        conn.execute(
            "UPDATE per_recitation_releases "
            "SET stale_since = COALESCE(stale_since, ?), stale_reason = ? WHERE id = ?",
            (iso, rv, hf["id"]),
        )
        n += 1

    gh = conn.execute(
        "SELECT grr.release_id, grr.slug, grr.stale_since, grr.stale_reason "
        "FROM gh_release_recitations grr JOIN gh_releases gr ON gr.id = grr.release_id "
        "WHERE grr.slug = ? AND gr.superseded_at IS NULL "
        "ORDER BY gr.id DESC LIMIT 1",
        (slug,),
    ).fetchone()
    if "gh" in tracks and gh is not None and _should_stamp(dict(gh), reason):
        conn.execute(
            "UPDATE gh_release_recitations "
            "SET stale_since = COALESCE(stale_since, ?), stale_reason = ? "
            "WHERE release_id = ? AND slug = ?",
            (iso, rv, gh["release_id"], gh["slug"]),
        )
        n += 1
    return n


def mark_ts_refreshed(
    slug: str,
    *,
    at: datetime,
    chapters: list[int] | None = None,
    reason: str | None = None,
) -> bool:
    """Record an out-of-band TS shard refresh for ``slug``. Idempotent.

    A manual backfill / schema-bump re-stamp / local regen uploads shards to the
    bucket outside the HF-job completion path, so the current ``ts`` release row
    is left pointing at a stale ``produced_at``. This advances that watermark to
    ``at`` (or now), which makes ``ts_staleness.ts_stale_info`` recompute clean
    (edits before the refresh no longer count) and lets the automation
    reconciler notice the refresh. It also stamps the slug's HF + most-recent-GH
    membership stale with ``TS_REGEN`` (the published dataset is now behind the
    refreshed shards), mirroring a normal regen, and appends a
    ``reciter.ts_refreshed`` audit row carrying ``{chapters, reason}``.

    Returns ``True`` when a current ``ts`` row existed and was advanced, ``False``
    when the reciter has no current ``ts`` release (nothing to refresh — a
    backfill on a never-generated reciter). Runs inside the caller's active
    ``durable_transaction``.
    """
    from services.segments.auto_detect import SYSTEM_ACTOR
    from services.state import audit

    conn = get_conn()
    iso = _serde.to_iso(at)
    cur = conn.execute(
        "UPDATE per_recitation_releases SET produced_at = ? "
        "WHERE track = 'ts' AND slug = ? AND superseded_at IS NULL",
        (iso, slug),
    )
    if cur.rowcount == 0:
        return False
    stamp_stale(slug, at=at, reason=StaleReason.TS_REGEN)
    audit.append(
        "reciter.ts_refreshed",
        actor=SYSTEM_ACTOR,
        slug=slug,
        from_state="released",
        to_state="released",
        payload={"chapters": chapters or [], "reason": reason},
    )
    return True


def stamp_stale_for_reciter(reciter_id: str, *, at: datetime, reason: StaleReason | str) -> int:
    """Fan ``stamp_stale`` out across every delivery of ``reciter_id`` (a
    reciter-level catalog field appears in each delivery's catalog projection).
    Returns the total stamps applied."""
    slugs = [
        r["slug"]
        for r in get_conn()
        .execute("SELECT slug FROM deliveries WHERE reciter_id = ?", (reciter_id,))
        .fetchall()
    ]
    return sum(stamp_stale(s, at=at, reason=reason) for s in slugs)


def clear_catalog_stale_hf() -> int:
    """Clear staleness from current ``hf`` rows stale *solely* due to a catalog
    edit (a global ``mushafs`` refresh rebuilds the whole catalog parquet from
    canonical). Leaves ``ts_regen`` rows stale — those still need a republish.
    Returns the number of rows cleared."""
    cur = get_conn().execute(
        "UPDATE per_recitation_releases SET stale_since = NULL, stale_reason = NULL "
        "WHERE track = 'hf' AND stale_since IS NOT NULL AND stale_reason = ?",
        (StaleReason.CATALOG_EDIT.value,),
    )
    return int(cur.rowcount)


# ---------------------------------------------------------------------------
# gh_releases + gh_release_recitations
# ---------------------------------------------------------------------------


def insert_gh_release(
    *,
    version: str,
    produced_at: datetime,
    produced_by: str,
    produced_by_job_id: str | None = None,
    launched_by: str | None = None,
    external_uri: str | None = None,
    source_catalog_rev: str | None = None,
    operator_note: str | None = None,
    validation_summary: dict[str, Any] | None = None,
) -> int:
    cur = get_conn().execute(
        "INSERT INTO gh_releases("
        " version, produced_at, produced_by, produced_by_job_id, launched_by,"
        " external_uri, source_catalog_rev, operator_note, validation_summary"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        (
            version,
            _serde.to_iso(produced_at),
            produced_by,
            produced_by_job_id,
            launched_by,
            external_uri,
            source_catalog_rev,
            operator_note,
            _serde.json_dumps(validation_summary) if validation_summary else None,
        ),
    )
    return _require_rowid(cur.lastrowid)


def supersede_prior_gh_releases(*, except_id: int, at: datetime) -> int:
    """Mark prior non-superseded gh_releases rows as superseded."""
    cur = get_conn().execute(
        "UPDATE gh_releases SET superseded_at = ? WHERE id != ? AND superseded_at IS NULL",
        (_serde.to_iso(at), except_id),
    )
    return int(cur.rowcount)


def latest_gh_release() -> dict | None:
    """Most-recent non-superseded GH release row or None."""
    r = (
        get_conn()
        .execute("SELECT * FROM gh_releases WHERE superseded_at IS NULL ORDER BY id DESC LIMIT 1")
        .fetchone()
    )
    return dict(r) if r else None


def latest_gh_release_summary() -> dict | None:
    """Most-recent gh_release + aggregated member metrics in one row.

    Returns ``{id, version, produced_at, external_uri, member_count, total_bytes}``
    or None when no release has been cut yet. Feeds the Releases-tab summary
    card so the FE can render ``v0.4.2 · 42 recitations · 11.8 MB · cut 7d ago``
    without a second roundtrip + per-member SUM on the FE.
    """
    r = (
        get_conn()
        .execute(
            "SELECT gr.id, gr.version, gr.produced_at, gr.external_uri, "
            "       COUNT(grr.slug)            AS member_count, "
            "       COALESCE(SUM(grr.zip_bytes), 0) AS total_bytes "
            "FROM gh_releases gr "
            "LEFT JOIN gh_release_recitations grr ON grr.release_id = gr.id "
            "WHERE gr.superseded_at IS NULL "
            "GROUP BY gr.id "
            "ORDER BY gr.id DESC LIMIT 1"
        )
        .fetchone()
    )
    return dict(r) if r else None


def insert_gh_release_recitation(
    *,
    release_id: int,
    slug: str,
    catalog_snapshot: dict[str, Any],
    zip_sha256: str,
    zip_bytes: int,
    coverage_ayahs: int,
    content_hash: str | None,
    ts_version: str,
    change_kind: str,
) -> None:
    get_conn().execute(
        "INSERT INTO gh_release_recitations("
        " release_id, slug, catalog_snapshot, zip_sha256, zip_bytes,"
        " coverage_ayahs, content_hash, ts_version, change_kind"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        (
            release_id,
            slug,
            _serde.json_dumps(catalog_snapshot),
            zip_sha256,
            zip_bytes,
            coverage_ayahs,
            content_hash,
            ts_version,
            change_kind,
        ),
    )


def gh_release_recitations(release_id: int) -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT * FROM gh_release_recitations WHERE release_id = ? ORDER BY slug",
            (release_id,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def latest_gh_release_member(slug: str) -> dict | None:
    """Most-recent gh_release_recitations row for ``slug`` across all releases."""
    r = (
        get_conn()
        .execute(
            "SELECT grr.* FROM gh_release_recitations grr "
            "JOIN gh_releases gr ON gr.id = grr.release_id "
            "WHERE grr.slug = ? AND gr.superseded_at IS NULL "
            "ORDER BY gr.id DESC LIMIT 1",
            (slug,),
        )
        .fetchone()
    )
    return dict(r) if r else None
