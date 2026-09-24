"""Shared usage budget for the align pipeline.

Every aligner call rides the Inspector's own HF token, so every GPU run spends
the owner's ZeroGPU quota whoever clicked Align. Maintainers therefore share one
budget: ``GPU_RUNS_PER_WINDOW`` GPU starts per rolling ``WINDOW`` and
``CPU_CONCURRENT`` CPU runs executing at once (CPU is otherwise unlimited).
A holder of ``EXEMPT_CAPABILITY`` (the owner by default) bypasses both, and the
runs they start are stamped ``quota_exempt`` so they never count.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from qua_shared.schemas import AlignQuota
from services.db import _serde, repo_align_runs

from .params import DEVICE_CPU, DEVICE_GPU, AlignParams

GPU_RUNS_PER_WINDOW = 2
CPU_CONCURRENT = 1
WINDOW = timedelta(hours=24)
EXEMPT_CAPABILITY = "intake.align_unlimited"


class AlignLimitReached(Exception):
    """A start/retry the shared budget refuses (route maps to 429)."""

    status = 429


def _counted_gpu_starts(now: datetime) -> list[datetime]:
    since = _serde.to_iso(now - WINDOW) or ""
    starts = [_serde.from_iso(s) for s in repo_align_runs.counted_starts_since(DEVICE_GPU, since)]
    # String prefilter is coarse; the parsed compare is the real window edge.
    return [s for s in starts if s is not None and s >= now - WINDOW]


def quota(*, exempt: bool) -> AlignQuota:
    now = datetime.now(UTC)
    starts = _counted_gpu_starts(now)
    resets_at = _serde.to_iso(starts[0] + WINDOW) if starts else None
    return AlignQuota(
        gpu_used=len(starts),
        gpu_limit=GPU_RUNS_PER_WINDOW,
        gpu_resets_at=resets_at,
        cpu_running=repo_align_runs.counted_running(DEVICE_CPU),
        cpu_limit=CPU_CONCURRENT,
        exempt=exempt,
    )


def check_start(device: str, *, exempt: bool) -> None:
    """Raise ``AlignLimitReached`` when a new ``device`` run would exceed the budget."""
    if exempt:
        return
    if device == DEVICE_GPU:
        starts = _counted_gpu_starts(datetime.now(UTC))
        if len(starts) >= GPU_RUNS_PER_WINDOW:
            frees = (starts[0] + WINDOW).strftime("%Y-%m-%d %H:%M UTC")
            raise AlignLimitReached(
                f"GPU limit reached: {GPU_RUNS_PER_WINDOW} GPU runs per 24 h are shared by "
                f"all maintainers. Next slot frees at {frees} — or run it on CPU."
            )
    elif device == DEVICE_CPU:
        _check_cpu_slot(exclude_run_id=None)


def check_retry(run: dict) -> None:
    """A retry re-occupies its lane: a counted CPU run needs the CPU slot free.

    GPU retries resume the same (already counted) start, so they pass."""
    params = AlignParams.from_json(run.get("params_json"))
    if params.device == DEVICE_CPU and not params.quota_exempt:
        _check_cpu_slot(exclude_run_id=run["run_id"])


def _check_cpu_slot(*, exclude_run_id: str | None) -> None:
    if repo_align_runs.counted_running(DEVICE_CPU, exclude_run_id=exclude_run_id) >= CPU_CONCURRENT:
        raise AlignLimitReached(
            f"CPU limit reached: {CPU_CONCURRENT} CPU run at a time is shared by all "
            "maintainers. Try again when the running one finishes."
        )
