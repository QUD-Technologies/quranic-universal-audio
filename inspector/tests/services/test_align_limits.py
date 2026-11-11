"""Shared align budget — 2 GPU starts per rolling 24 h, 1 CPU run at a time,
counted across every non-exempt caller; the owner (exempt) bypasses both."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from qua_shared.schemas import Actor, Role
from services.admin import requests as admin_requests
from services.admin.align_pipeline import limits, runs
from services.admin.align_pipeline.params import AlignParams
from services.db import _serde, repo_align_runs
from services.db.connection import get_conn
from services.db.sync import durable_transaction
from tests.conftest import _seed_delivery_chain
from tests.services.test_align_pipeline import SLUG, align_env  # noqa: F401 — fixture

MAINTAINER = Actor(hf_user_id="u-maint", login_at_time="maint", role=Role.MAINTAINER)


def _seed_run(
    run_id: str,
    *,
    device: str,
    status: str = "succeeded",
    hours_ago: float = 1,
    exempt: bool = False,
) -> None:
    """One finished/active run on its own delivery (one active run per slug)."""
    slug = f"{SLUG}_{run_id}"
    started = _serde.to_iso(datetime.now(UTC) - timedelta(hours=hours_ago))
    with durable_transaction():
        conn = get_conn()
        _seed_delivery_chain(conn, slug)
        repo_align_runs.insert(
            run_id=run_id,
            slug=slug,
            requested_by="u-x",
            params_json=AlignParams(device=device, quota_exempt=exempt).to_json(),
            chapters_total=1,
        )
        repo_align_runs.update(run_id, status=status)
        conn.execute("UPDATE align_runs SET started_at = ? WHERE run_id = ?", (started, run_id))


def test_gpu_budget_counts_rolling_window_and_skips_exempt(align_env):  # noqa: F811
    _seed_run("old", device="GPU", hours_ago=25)  # aged out
    _seed_run("owner", device="GPU", hours_ago=2, exempt=True)  # never counts
    _seed_run("g1", device="GPU", hours_ago=20)
    limits.check_start("GPU", exempt=False)  # 1/2 — still room

    _seed_run("g2", device="GPU", hours_ago=3)
    with pytest.raises(limits.AlignLimitReached, match="GPU limit reached"):
        limits.check_start("GPU", exempt=False)
    limits.check_start("GPU", exempt=True)  # the owner bypasses
    limits.check_start("CPU", exempt=False)  # CPU has no daily cap

    q = limits.quota(exempt=False)
    assert (q.gpu_used, q.gpu_limit) == (2, 2)
    assert q.gpu_resets_at is not None
    frees = datetime.fromisoformat(q.gpu_resets_at.replace("Z", "+00:00"))
    assert timedelta(hours=3) < frees - datetime.now(UTC) < timedelta(hours=5)


def test_cpu_allows_one_counted_run_at_a_time(align_env):  # noqa: F811
    _seed_run("gpu-live", device="GPU", status="running")  # other lane
    _seed_run("cpu-owner", device="CPU", status="running", exempt=True)
    limits.check_start("CPU", exempt=False)

    _seed_run("cpu-done", device="CPU", status="canceled")
    _seed_run("cpu-live", device="CPU", status="running")
    with pytest.raises(limits.AlignLimitReached, match="CPU limit reached"):
        limits.check_start("CPU", exempt=False)
    limits.check_start("CPU", exempt=True)
    assert limits.quota(exempt=True).cpu_running == 1


def test_retry_of_a_cpu_run_needs_the_slot(align_env):  # noqa: F811
    _seed_run("cpu-live", device="CPU", status="running")
    failed_cpu = {"run_id": "f", "params_json": AlignParams(device="CPU").to_json()}
    with pytest.raises(limits.AlignLimitReached):
        limits.check_retry(failed_cpu)
    # The running run retrying itself (e.g. after a restart) is not its own blocker.
    limits.check_retry({"run_id": "cpu-live", "params_json": failed_cpu["params_json"]})
    limits.check_retry({"run_id": "g", "params_json": AlignParams(device="GPU").to_json()})


def test_start_refuses_past_budget_and_stamps_exempt_runs(align_env):  # noqa: F811
    _seed_run("g1", device="GPU", hours_ago=5)
    _seed_run("g2", device="GPU", hours_ago=4)
    with pytest.raises(runs.AlignRunError) as exc:
        runs.start(SLUG, MAINTAINER, device="GPU")
    assert exc.value.status == 429
    assert repo_align_runs.active_for_slug(SLUG) is None  # nothing was written

    status = runs.start(SLUG, MAINTAINER, device="GPU", exempt=True)
    row = repo_align_runs.get(status.run_id)
    assert row is not None
    params = AlignParams.from_json(row["params_json"])
    assert params.quota_exempt is True


def test_requests_payload_carries_the_budget(align_env):  # noqa: F811
    _seed_run("g1", device="GPU")
    payload = admin_requests.list_requests(
        status="open", caller_is_owner=False, caller_hf_id="u-maint", align_exempt=False
    )
    assert payload["align_quota"]["gpu_used"] == 1
    assert payload["align_quota"]["exempt"] is False
    no_align = admin_requests.list_requests(
        status="open", caller_is_owner=False, caller_hf_id="u-maint"
    )
    assert "align_quota" not in no_align
