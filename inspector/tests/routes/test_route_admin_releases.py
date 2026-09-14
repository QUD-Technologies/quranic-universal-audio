"""Tests for the admin Releases-tab routes.

Covers:
- ``GET /api/admin/releases/status``:
  * a released reciter with a current ledger row is bucketable (lands in
    "Waiting to publish" when there's no HF row yet).
  * inert rows (no TS / HF / GH / not released) are dropped by the
    ``_is_bucketable`` server-side filter — the wire payload only contains
    rows the FE will actually render.
  * eligible vs ineligible channel scoping for the "Excluded" bucket.
  * a slug appearing in ``in_flight`` is bucketable even if it would
    otherwise be inert.
- ``POST /api/admin/publish-hf/<slug>``:
  * 409s when no current ledger row exists (the gate is strict — the
    canonical fix is to backfill the ledger via ``admin_db.py``, not to
    let the route guess from delivery state).

The autouse ``_substrate_db`` fixture gives us a fresh migrated SQLite per
test. ``list_in_flight_jobs`` is mocked to ``[]`` so we don't hit the live
HF Jobs API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_jobs_api(monkeypatch, *, in_flight=(), batch_outcome=None):
    """Stub the HF Jobs API so route tests don't hit the network.

    Pass ``in_flight`` as a list of ``{kind, slug, job_id, started_at, url}``
    dicts to simulate live jobs. ``batch_outcome`` stubs the newest batch
    record read by the status endpoint (defaults to None — no prior batch).
    """
    from services.admin.jobs import base as jobs_base
    from services.admin.jobs import hf_publish_batch as batch_jobs

    monkeypatch.setattr(
        jobs_base, "list_in_flight_jobs", lambda kinds, *, block=True: list(in_flight)
    )
    monkeypatch.setattr(jobs_base, "running_job_for", lambda **_: None)
    monkeypatch.setattr(batch_jobs, "latest_batch_outcome", lambda: batch_outcome)


def _seed_eligible_channel(slug: str = "mp3quran") -> None:
    """Insert a channel row with ``gh_release_eligible = 1``. Migration 0014
    only flips the flag on rows that exist at migration time; tests INSERT
    after migrations run, so we set it explicitly."""
    from services import db

    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO channels(slug,short,name,gh_release_eligible) VALUES (?,?,?,1)",
            (slug, slug[:1], slug),
        )


def _seed_ineligible_channel(slug: str = "private_ch") -> None:
    from services import db

    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO channels(slug,short,name,gh_release_eligible) VALUES (?,?,?,0)",
            (slug, slug[:1], slug),
        )


def _seed_delivery_on_channel(slug: str, *, channel: str, reciter_id: str = "r1") -> None:
    """Seed the FK chain for a delivery on a specific channel (the conftest
    helper only knows about ``channel='ch'``)."""
    from services import db

    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO reciters(reciter_id,name_en,name_ar) VALUES (?,?,?)",
            (reciter_id, reciter_id.upper(), f"AR-{reciter_id}"),
        )
        conn.execute("INSERT OR IGNORE INTO riwayahs(slug,short,name) VALUES ('hafs','h','Hafs')")
        conn.execute("INSERT OR IGNORE INTO styles(slug,short,name) VALUES ('mur','m','Mur')")
        conn.execute("INSERT OR IGNORE INTO sources(slug,name) VALUES ('src','Src')")
        conn.execute(
            "INSERT OR IGNORE INTO deliveries(slug,reciter_id,riwayah,style,source,channel,"
            "audio_category,chapter_count,added_at,added_by_hf_id) VALUES "
            "(?,?,'hafs','mur','src',?,'by_surah',114,'2026-01-01T00:00:00Z','sys')",
            (slug, reciter_id, channel),
        )


def _seed_released(slug: str, *, channel: str = "mp3quran", reciter_id: str = "r1") -> None:
    """Seed a released-state delivery on an eligible channel."""
    from qua_shared.schemas import ReciterState, Visibility
    from services import db
    from services.db import repo_state

    _seed_eligible_channel(channel)
    _seed_delivery_on_channel(slug, channel=channel, reciter_id=reciter_id)
    with db.transaction():
        repo_state.upsert_state(
            slug,
            state=ReciterState.RELEASED,
            state_since=datetime.now(UTC),
            visibility=Visibility.PUBLIC,
        )


def _seed_ledger_ts(slug: str, version: str = "real-job-id") -> None:
    """Insert a current track='ts' ledger row for ``slug``."""
    from services import db
    from services.db import repo_releases

    with db.transaction():
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version=version,
            produced_at=datetime.now(UTC),
            produced_by="test",
        )


def _seed_catalog_stale_hf(slug: str, version: str = "hf-rev") -> None:
    """Insert a current track='hf' row for ``slug`` and stamp it catalog_edit-stale."""
    from qua_shared.schemas import StaleReason
    from services import db
    from services.db import repo_releases

    with db.transaction():
        repo_releases.insert_per_recitation_release(
            track="hf",
            slug=slug,
            version=version,
            produced_at=datetime.now(UTC),
            produced_by="test",
        )
        repo_releases.stamp_stale(slug, at=datetime.now(UTC), reason=StaleReason.CATALOG_EDIT)


# ---------------------------------------------------------------------------
# /api/admin/releases/status
# ---------------------------------------------------------------------------


def test_status_released_with_ledger_is_waiting(signed_in_client, monkeypatch):
    """A released-state reciter with a current track='ts' ledger row appears
    in the response — the FE will bucket it as "Waiting to publish" because
    state='released', ts != null, hf == null."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar.ready_to_publish", channel="mp3quran")
    _seed_ledger_ts("ar.ready_to_publish", version="real-job-id-123")

    resp = client.get("/api/admin/releases/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    rows = body["recitations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["slug"] == "ar.ready_to_publish"
    assert row["state"] == "released"
    assert row["ts"] == {
        "version": "real-job-id-123",
        "produced_at": row["ts"]["produced_at"],
        "stale_since": None,
        "stale_reason": None,
        "suggested_action": None,
        "edits_since": None,
        "affected_chapters": None,
    }
    assert row["hf"] is None
    assert row["gh"] is None


def test_status_released_without_ledger_is_filtered(signed_in_client, monkeypatch):
    """A released-state row with NO ledger entry is dropped — the ledger
    is the source of truth for TS-existence. The canonical fix for that
    state is a one-shot backfill via ``admin_db.py``, not a route bridge."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar.no_ledger", channel="mp3quran")
    # No _seed_ledger_ts call — the ledger stays empty.

    resp = client.get("/api/admin/releases/status")
    body = resp.get_json()
    assert body["recitations"] == []


def test_status_inert_rows_filtered(signed_in_client, monkeypatch, seed_state):
    """Rows with no TS, no HF, no GH membership, and not released are
    dropped by ``_is_bucketable`` — they have no business in the Releases
    tab (operator can't do anything with them there)."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_eligible_channel("mp3quran")

    # Released + ledgered → bucketable.
    _seed_released("ar.visible", channel="mp3quran")
    _seed_ledger_ts("ar.visible")
    # Awaiting review — no TS, no HF, no GH — dropped.
    _seed_delivery_on_channel("ar.inert", channel="mp3quran", reciter_id="r2")
    seed_state("ar.inert", state="awaiting_review")

    resp = client.get("/api/admin/releases/status")
    body = resp.get_json()
    slugs = [r["slug"] for r in body["recitations"]]
    assert slugs == ["ar.visible"]


def test_status_ignores_channel_eligibility_flag(signed_in_client, monkeypatch, seed_state):
    """The channel ``gh_release_eligible`` flag no longer gates anything. A
    released reciter with a current TS row on a flag-0 channel surfaces as a
    normal bucketable row, and the eligibility field is gone from the wire."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_ineligible_channel("private_ch")
    _seed_delivery_on_channel("ar.flag_off", channel="private_ch", reciter_id="r3")
    seed_state("ar.flag_off", state="released")
    _seed_ledger_ts("ar.flag_off")

    resp = client.get("/api/admin/releases/status")
    body = resp.get_json()
    row = next(r for r in body["recitations"] if r["slug"] == "ar.flag_off")
    assert row["state"] == "released" and row["ts"] is not None
    assert "gh_release_eligible" not in row


def test_status_surfaces_catalog_edit_drift_and_suggestion(signed_in_client, monkeypatch):
    """A catalog_edit-stale HF row carries stale_reason + the resolved
    refresh suggestion, and bumps the top-level catalog_drift_count."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar.drifted", channel="mp3quran")
    _seed_ledger_ts("ar.drifted")
    _seed_catalog_stale_hf("ar.drifted")

    resp = client.get("/api/admin/releases/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    row = next(r for r in body["recitations"] if r["slug"] == "ar.drifted")
    assert row["hf"]["stale_since"] is not None
    assert row["hf"]["stale_reason"] == "catalog_edit"
    assert row["hf"]["suggested_action"]["action"] == "refresh_hf_catalog"
    assert row["hf"]["suggested_action"]["kind"] == "actionable"
    assert body["catalog_drift_count"] == 1


def test_status_surfaces_ts_segments_edited_staleness(signed_in_client, monkeypatch, seed_state):
    """A reciter whose segments were edited after generation carries the
    computed ``segments_edited`` staleness + regen suggestion on its TS track,
    and is bucketable even though it's neither released nor published."""
    from services.segments import ts_staleness

    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_eligible_channel("mp3quran")
    _seed_delivery_on_channel("ar.edited", channel="mp3quran", reciter_id="r9")
    seed_state("ar.edited", state="under_review")  # not released, no HF row
    _seed_ledger_ts("ar.edited")
    monkeypatch.setattr(
        ts_staleness,
        "ts_stale_info",
        lambda slug, *, produced_at: (
            {"stale_since": "2026-03-02T00:00:00Z", "edits_since": 3, "affected_chapters": [5, 12]}
            if slug == "ar.edited"
            else None
        ),
    )

    resp = client.get("/api/admin/releases/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    row = next(r for r in body["recitations"] if r["slug"] == "ar.edited")
    assert row["ts"]["stale_reason"] == "segments_edited"
    assert row["ts"]["edits_since"] == 3
    assert row["ts"]["affected_chapters"] == [5, 12]
    assert row["ts"]["suggested_action"]["action"] == "regenerate_ts"
    assert row["ts"]["suggested_action"]["capability"] == "reviews.generate_timestamps"
    assert row["hf"] is None  # purely TS-track staleness


def test_status_flagged_count_only_on_ready_to_generate(signed_in_client, monkeypatch, seed_state):
    """``flagged_issues_count`` is computed ONLY for Ready-to-generate rows
    (marked-ready, no TS yet); every other bucketable row leaves it None."""
    from routes.admin import releases as releases_route

    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)

    # Ready-to-generate: under_review + marked-ready, no ts ledger row.
    _seed_eligible_channel("mp3quran")
    _seed_delivery_on_channel("ar.rtg", channel="mp3quran", reciter_id="r7")
    seed_state("ar.rtg", state="under_review", assignee_hf_id="u1", marked_ready=True)

    # Released + has a ts ledger row → bucketable but NOT ready-to-generate.
    _seed_released("ar.done", channel="mp3quran", reciter_id="r8")
    _seed_ledger_ts("ar.done")

    # Sentinel: 99 must never surface (ar.done is not ready-to-generate, so the
    # count is never read for it).
    monkeypatch.setattr(
        releases_route, "_flagged_count", lambda slug: 3 if slug == "ar.rtg" else 99
    )

    resp = client.get("/api/admin/releases/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    rtg = next(r for r in body["recitations"] if r["slug"] == "ar.rtg")
    done = next(r for r in body["recitations"] if r["slug"] == "ar.done")
    assert rtg["flagged_issues_count"] == 3
    assert done.get("flagged_issues_count") is None


def test_refresh_hf_catalog_launches(signed_in_client, monkeypatch):
    """POST /api/admin/refresh-hf-catalog launches the job and returns 202."""
    from services.admin.jobs import refresh_catalog as refresh_jobs

    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    monkeypatch.setattr(
        refresh_jobs, "launch", lambda **_: {"job_id": "job-x", "url": "https://hf/job-x"}
    )

    resp = client.post("/api/admin/refresh-hf-catalog", headers=_HEADERS)
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json()["job_id"] == "job-x"


def test_refresh_hf_catalog_rejects_while_in_flight(signed_in_client, monkeypatch):
    """A second refresh is rejected (409) while one is already running."""
    from services.admin.jobs import base as jobs_base

    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    monkeypatch.setattr(
        jobs_base,
        "running_job_for",
        lambda **kw: ("refresh_catalog", "j1") if kw.get("kind") == "refresh_catalog" else None,
    )

    resp = client.post("/api/admin/refresh-hf-catalog", headers=_HEADERS)
    assert resp.status_code == 409, resp.get_data(as_text=True)


def test_status_in_flight_slug_is_bucketable(signed_in_client, monkeypatch):
    """A slug appearing in ``in_flight`` is bucketable even if it would
    otherwise be inert — the operator needs to see the running job."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(
        monkeypatch,
        in_flight=[
            {
                "kind": "hf_publish",
                "slug": "ar.in-flight",
                "job_id": "j_abc",
                "started_at": "2026-06-01T12:00:00",
            }
        ],
    )
    _seed_eligible_channel("mp3quran")
    _seed_delivery_on_channel("ar.in-flight", channel="mp3quran", reciter_id="r5")
    # No state, no ledger — inert except for the live job.

    resp = client.get("/api/admin/releases/status")
    body = resp.get_json()
    slugs = [r["slug"] for r in body["recitations"]]
    assert "ar.in-flight" in slugs


# ---------------------------------------------------------------------------
# /api/admin/release-preview
# ---------------------------------------------------------------------------


def test_release_preview_uses_display_names(signed_in_client, monkeypatch):
    """The dry-run preview returns display names (NOT slugs), surah coverage,
    change_kind, and a rendered body from the shared renderer with the title
    line + display names and no slug leakage. Owner-only (release.cut_gh)."""
    client, _user = signed_in_client(role="owner")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar_preview", channel="mp3quran", reciter_id="r1")
    _seed_ledger_ts("ar_preview", version="job-123")

    resp = client.get("/api/admin/release-preview")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    assert body["computed_version"] == "v0.1.0"
    assert body["previous_version"] is None
    assert body["change_counts"]["added"] == 1
    assert len(body["added"]) == 1
    row = body["added"][0]
    # Display names from the vocab tables — never the FK slugs.
    assert row["riwayah"] == "Hafs"
    assert row["style"] == "Mur"
    assert row["channel"] == "mp3quran"
    assert row["name_ar"] == "AR-r1"
    assert row["coverage_surahs"] == 114
    assert row["change_kind"] == "added"

    # Top-level additions for the modal.
    assert body["license"] == "CC-BY-4.0"
    assert "repo" in body["links"] and "hf_dataset" in body["links"]
    assert body["release_date"]

    md = body["changelog_preview_md"]
    assert md.startswith("# ")
    assert "\n\n## What to download" in md
    assert "`catalog.json`" in md and "audio URLs paired with the timestamp data" in md
    assert "`release_schemas.json`" not in md
    assert "Hafs" in md and "114 surahs" in md
    # The delivery slug must not leak into the human-facing body.
    assert "ar_preview" not in md


# ---------------------------------------------------------------------------
# /api/admin/cut-release
# ---------------------------------------------------------------------------


def test_cut_release_rejects_operator_note_field(signed_in_client, monkeypatch):
    client, _user = signed_in_client(role="owner")
    _stub_jobs_api(monkeypatch)

    resp = client.post(
        "/api/admin/cut-release",
        json={"version": "v1.0.0", "operator_note": "removed"},
        headers=_HEADERS,
    )

    assert resp.status_code == 400
    assert "invalid cut-release request" in resp.get_json()["error"]


def test_cut_release_launch_accepts_schema_body_only(signed_in_client, monkeypatch):
    client, _user = signed_in_client(role="owner")
    _stub_jobs_api(monkeypatch)

    from services.admin.jobs import cut_release as cut_release_jobs

    called = {}

    def fake_launch(**kwargs):
        called.update(kwargs)
        return {"job_id": "j_cut", "url": None}

    monkeypatch.setattr(cut_release_jobs, "launch", fake_launch)

    resp = client.post(
        "/api/admin/cut-release",
        json={"version": "v1.0.0", "expected_version_at_preview": None},
        headers=_HEADERS,
    )

    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json() == {"job_id": "j_cut", "url": None}
    assert called["version"] == "v1.0.0"
    assert "operator_note" not in called


# ---------------------------------------------------------------------------
# /api/admin/publish-hf/<slug>
# ---------------------------------------------------------------------------


def test_publish_hf_accepts_ledgered_slug(signed_in_client, monkeypatch):
    """publish-hf accepts a slug that has a current track='ts' ledger row."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar_ready", channel="mp3quran")
    _seed_ledger_ts("ar_ready")

    from services.admin.jobs import hf_publish as hf_publish_jobs

    monkeypatch.setattr(
        hf_publish_jobs,
        "launch",
        lambda slug, webhook_base=None: {"job_id": "j_test", "url": None},
    )

    resp = client.post("/api/admin/publish-hf/ar_ready", headers=_HEADERS)
    assert resp.status_code == 202, resp.get_data(as_text=True)


def test_publish_hf_rejects_no_ledger(signed_in_client, monkeypatch, seed_state):
    """Without a ledger row publish-hf 409s — the ledger is the source of
    truth for TS-existence. Released-state alone is not sufficient (backfill
    the ledger via admin_db.py for legacy slugs)."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_eligible_channel("mp3quran")
    _seed_delivery_on_channel("ar_no_ledger", channel="mp3quran", reciter_id="r6")
    seed_state("ar_no_ledger", state="released")

    resp = client.post("/api/admin/publish-hf/ar_no_ledger", headers=_HEADERS)
    assert resp.status_code == 409
    assert "has no current TS release" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# /api/admin/publish-hf-batch
# ---------------------------------------------------------------------------


def test_publish_hf_batch_launches_with_deduped_slugs(signed_in_client, monkeypatch):
    """A batch of ledgered slugs launches one job; the route de-dups slugs
    (preserving order) before handing them to the launcher."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar_b1", channel="mp3quran", reciter_id="rb1")
    _seed_ledger_ts("ar_b1")
    _seed_released("ar_b2", channel="mp3quran", reciter_id="rb2")
    _seed_ledger_ts("ar_b2")

    from services.admin.jobs import hf_publish_batch as batch_jobs

    seen = {}
    monkeypatch.setattr(
        batch_jobs,
        "launch",
        lambda slugs, *, settings=None, webhook_base=None: (
            seen.update(slugs=slugs) or {"job_id": "j_b", "url": "u"}
        ),
    )

    resp = client.post(
        "/api/admin/publish-hf-batch",
        json={"slugs": ["ar_b1", "ar_b2", "ar_b1"]},
        headers=_HEADERS,
    )
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert resp.get_json() == {"job_id": "j_b", "url": "u"}
    assert seen["slugs"] == ["ar_b1", "ar_b2"]


def test_publish_hf_batch_rejects_member_without_ledger(signed_in_client, monkeypatch):
    """If any selected slug lacks a current TS row the whole batch 409s — no
    partial launch."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(monkeypatch)
    _seed_released("ar_have_ts", channel="mp3quran", reciter_id="rb3")
    _seed_ledger_ts("ar_have_ts")
    _seed_released("ar_no_ts", channel="mp3quran", reciter_id="rb4")  # no ledger

    resp = client.post(
        "/api/admin/publish-hf-batch",
        json={"slugs": ["ar_have_ts", "ar_no_ts"]},
        headers=_HEADERS,
    )
    assert resp.status_code == 409
    assert "ar_no_ts" in resp.get_json()["error"]


def test_status_surfaces_publish_error_and_last_batch(signed_in_client, monkeypatch):
    """A slug failed in the most-recent batch (and not since republished) gets
    a ``publish_error`` on its row and is reflected in the ``last_batch``
    summary banner payload."""
    client, _user = signed_in_client(role="maintainer")
    _stub_jobs_api(
        monkeypatch,
        batch_outcome={
            "job_id": "j_batch",
            "completed_at": "2026-06-05T10:00:00Z",
            "members": [
                {"slug": "ar_failed", "status": "failed", "error": "every audio slice failed"},
            ],
        },
    )
    # The failed slug is released + ledgered (waiting to publish) but carries no
    # HF row → the failure is still live.
    _seed_released("ar_failed", channel="mp3quran", reciter_id="rb5")
    _seed_ledger_ts("ar_failed")

    resp = client.get("/api/admin/releases/status")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    row = next(r for r in body["recitations"] if r["slug"] == "ar_failed")
    assert row["publish_error"]["message"] == "every audio slice failed"
    assert row["publish_error"]["job_id"] == "j_batch"
    assert body["last_batch"] == {
        "job_id": "j_batch",
        "at": "2026-06-05T10:00:00Z",
        "published_count": 0,
        "failed_count": 1,
    }
