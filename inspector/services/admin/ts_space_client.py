"""Sign + POST the batch timing Space's ``/internal/v1/timestamps`` route.

The whole-verse timestamps producer runs on the batch timing Space (ADR 0002
slice B), not an in-container HF job. The Inspector fires a run by POSTing a
production-HMAC-signed request; the Space returns a ``run_id`` and writes its
progress to the bucket run-log the Inspector polls.

The signature mirrors ``qua_contracts.signing`` (the engines' shared contract):
an RFC 8785 (JCS) digest of the JSON body, then HMAC-SHA256 over the 8-line
input. qua_contracts is not a QUA runtime dependency, so the small slice of JCS
this fixed request shape needs is inlined here and pinned by a parity test
against the real signer. The body is ints / strings / int-arrays only; a float
is rejected rather than silently mis-signed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH

# SHA-256 of empty bytes — the audio part is always absent (bucket-mount I/O).
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_ROUTE = "/internal/v1/timestamps"
_PROFILE_ID = "timing.timestamps@v1"
_SECRET_VERSION = "v1"

DEFAULT_SPACE_URL = "https://hetchyy-qua-batch-timing-prod.hf.space"
DEFAULT_SPACE_REPO = "hetchyy/qua-batch-timing-prod"

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


def _canonical(value: Any) -> str:
    """RFC 8785 (JCS) canonical JSON for the timestamps request shape only:
    objects (keys sorted by UTF-16 code units), int-arrays, strings, ints,
    bools, null. A float has no place in this request and is rejected so a
    schema drift fails closed instead of signing a wrong preimage."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: str(kv[0]).encode("utf-16-be"))
        return "{" + ",".join(f"{_canonical(str(k))}:{_canonical(v)}" for k, v in items) + "}"
    raise TypeError(f"{type(value).__name__} is not canonicalizable for timestamps signing")


def _sign_headers(body: dict, secret: bytes, hf_token: str | None) -> tuple[bytes, dict[str, str]]:
    """Serialize ``body``, compute the production-HMAC headers, return both."""
    raw = json.dumps(body).encode("utf-8")
    metadata_sha256 = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    timestamp = datetime.now(UTC).isoformat()
    nonce = uuid.uuid4().hex
    preimage = "\n".join(
        (
            _SECRET_VERSION,
            "POST",
            _ROUTE,
            timestamp,
            nonce,
            "",  # operation_id — the accept carries none
            metadata_sha256,
            _EMPTY_SHA256,
        )
    ).encode("utf-8")
    signature = hmac.new(secret, preimage, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Qua-Engine-Signature": f"{_SECRET_VERSION}={signature}",
        "X-Qua-Timestamp": timestamp,
        "X-Qua-Nonce": nonce,
    }
    if hf_token:  # private Space: the HF proxy needs the bearer too
        headers["Authorization"] = f"Bearer {hf_token}"
    return raw, headers


def _secret() -> bytes:
    """The v1 HMAC secret QUA signs with, from ``INSPECTOR_TS_ENGINE_SECRET``
    (hex). Same value the Space holds as ``QUA_ENGINE_HMAC_SECRETS`` v1."""
    raw = os.environ.get("INSPECTOR_TS_ENGINE_SECRET", "").strip()
    if not raw:
        raise TsSpaceError("INSPECTOR_TS_ENGINE_SECRET is not set")
    try:
        return bytes.fromhex(raw)
    except ValueError as exc:
        raise TsSpaceError("INSPECTOR_TS_ENGINE_SECRET is not valid hex") from exc


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

    raise TsSpaceError(
        f"timestamps Space did not wake within {_WAKE_TIMEOUT_SECONDS} seconds"
    )


def _post_run(body: dict[str, Any], secret: bytes, hf_token: str | None):
    """Sign and POST one accept attempt with a fresh timestamp and nonce."""
    import requests

    raw, headers = _sign_headers(body, secret, hf_token)
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
) -> str:
    """POST a signed timestamps run for ``slug``; return the Space ``run_id``.

    Raises :class:`TsSpaceError` on a non-2xx (a saturated pool 4xx included) or
    a malformed accept payload. The Space writes the ``running`` run-log record
    before it returns, so the caller can poll immediately.

    ``riwayah`` is the SDK slug the delivery is recited in. It is omitted from
    the body for Hafs on purpose: the canonicaliser sorts keys, so every
    existing Hafs preimage stays byte-identical and the Space-side change need
    only ship before the first non-Hafs run, not in lockstep with this one.
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

    secret = _secret()
    hf_token = get_token()
    resp = _post_run(body, secret, hf_token)
    if resp.status_code == 503 and _wake_unavailable_space(hf_token):
        # The proxy rejected the first request before the engine saw it. Sign a
        # new attempt after wake so its timestamp/nonce are fresh.
        resp = _post_run(body, secret, hf_token)
    if resp.status_code // 100 != 2:
        raise TsSpaceError(f"timestamps Space {resp.status_code}: {resp.text[:300]}")
    try:
        run_id = resp.json()["run_id"]
    except (ValueError, KeyError) as exc:
        raise TsSpaceError(f"timestamps Space returned no run_id: {resp.text[:200]}") from exc
    return str(run_id)
