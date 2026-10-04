"""POST the batch timing Space's ``/internal/v1/timestamps`` route.

The whole-verse timestamps producer runs on the batch timing Space (ADR 0002
slice B), not an in-container HF job. The Inspector fires a run by POSTing a
request through the private Space's HF token gate; the Space returns a ``run_id`` and writes its
progress to the bucket run-log the Inspector polls.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH

_ROUTE = "/internal/v1/timestamps"
_PROFILE_ID = "timing.timestamps@v1"

DEFAULT_SPACE_URL = "https://qud-technologies-qua-batch-timing-prod.hf.space"
DEFAULT_SPACE_REPO = "QUD-Technologies/qua-batch-timing-prod"

_WAKEABLE_STAGES = {"PAUSED", "SLEEPING"}
_STARTING_STAGES = {
    "BUILDING",
    "APP_STARTING",
    "RUNNING_BUILDING",
    "RUNNING_APP_STARTING",
}
_WAKE_TIMEOUT_SECONDS = 300
_WAKE_POLL_SECONDS = 3


class TsSpaceError(RuntimeError):
    """The Space rejected or could not accept the timestamps run."""


def space_url() -> str:
    return os.environ.get("INSPECTOR_TS_SPACE_URL", DEFAULT_SPACE_URL).rstrip("/")


def space_repo() -> str:
    """Hub repo backing :func:`space_url`, used only for lifecycle recovery."""
    return os.environ.get("INSPECTOR_TS_SPACE_REPO", DEFAULT_SPACE_REPO).strip()


def _wake_unavailable_space(hf_token: str | None) -> bool:
    """Wake a sleeping/paused timing Space and wait until it can accept work.

    Returns ``True`` when the caller should retry its POST. A 503 from a Space
    that Hub already considers RUNNING is left alone: that is an application
    failure, and factory-restarting it here would hide the real fault.
    """
    if not hf_token:
        return False

    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi(token=hf_token)
    repo_id = space_repo()
    try:
        runtime = api.space_info(repo_id).runtime
        stage = str(getattr(runtime, "stage", "") or "").upper()
        if stage in _WAKEABLE_STAGES:
            try:
                api.restart_space(repo_id=repo_id, factory_reboot=False)
            except HfHubHTTPError:
                # Another request may have won the wake race. Re-read below;
                # only a transition/running stage is accepted as recovery.
                runtime = api.space_info(repo_id).runtime
                stage = str(getattr(runtime, "stage", "") or "").upper()
                if stage not in _STARTING_STAGES and stage != "RUNNING":
                    raise
        elif stage not in _STARTING_STAGES:
            return False

        deadline = time.monotonic() + _WAKE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            runtime = api.space_info(repo_id).runtime
            stage = str(getattr(runtime, "stage", "") or "").upper()
            if stage == "RUNNING":
                return True
            if stage not in _WAKEABLE_STAGES and stage not in _STARTING_STAGES:
                raise TsSpaceError(
                    f"timestamps Space could not wake (Hub runtime stage {stage or 'unknown'})"
                )
            time.sleep(_WAKE_POLL_SECONDS)
    except TsSpaceError:
        raise
    except Exception as exc:  # noqa: BLE001 — Hub client has several transport errors
        raise TsSpaceError(f"timestamps Space wake failed: {exc}") from exc

    raise TsSpaceError(f"timestamps Space did not wake within {_WAKE_TIMEOUT_SECONDS} seconds")


def _post_run(body: dict[str, Any], hf_token: str):
    """POST one accept attempt through the private Space's HF gateway."""
    import requests

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {hf_token}"}
    try:
        return requests.post(space_url() + _ROUTE, data=raw, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise TsSpaceError(f"timestamps Space unreachable: {exc}") from exc


def start_run(
    slug: str,
    *,
    chapters: list[int] | None = None,
    beams: list[int] | None = None,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
    full: bool = False,
) -> str:
    """POST a timestamps run for ``slug``; return the Space ``run_id``.

    Raises :class:`TsSpaceError` on a non-2xx (a saturated pool 4xx included) or
    a malformed accept payload. The Space writes the ``running`` run-log record
    before it returns, so the caller can poll immediately.

    ``riwayah`` is the SDK slug the delivery is recited in. It is omitted from
    the body for Hafs to retain the established request shape.

    Inside ``chapters`` the Space re-aligns only the verses whose segments moved
    since the existing shard; ``full`` re-aligns every verse (of every chapter
    when ``chapters`` is empty).
    """
    from huggingface_hub import get_token

    body: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": _PROFILE_ID,
        "slug": slug,
    }
    if chapters:
        body["chapters"] = list(chapters)
    if beams:
        body["beams"] = list(beams)
    if riwayah != DEFAULT_SDK_RIWAYAH:
        body["riwayah"] = riwayah
    if full:
        body["full"] = True

    hf_token = get_token()
    if not hf_token:
        raise TsSpaceError("HF token with access to the private timestamps Space is required")
    resp = _post_run(body, hf_token)
    if resp.status_code == 503 and _wake_unavailable_space(hf_token):
        # The proxy rejected the first request before the engine saw it.
        resp = _post_run(body, hf_token)
    if resp.status_code // 100 != 2:
        raise TsSpaceError(f"timestamps Space {resp.status_code}: {resp.text[:300]}")
    try:
        run_id = resp.json()["run_id"]
    except (ValueError, KeyError) as exc:
        raise TsSpaceError(f"timestamps Space returned no run_id: {resp.text[:200]}") from exc
    return str(run_id)
