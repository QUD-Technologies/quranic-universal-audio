"""``services.admin.ts_space_client`` — the signed timestamps-run request.

The Space verifies an HMAC over a JCS digest of the body, so the exact bytes of
the canonical form are the contract. These tests pin that form, and in
particular that adding multi-riwayah support did NOT change the Hafs preimage:
the field is omitted for Hafs, so every existing Hafs run signs exactly as
before and the Space-side change can ship on its own schedule.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.admin import ts_space_client


@pytest.fixture
def posted(monkeypatch):
    """Capture the body ``start_run`` posts, without touching the network."""
    monkeypatch.setenv("INSPECTOR_TS_ENGINE_SECRET", "ab" * 32)
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)

    captured: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"run_id": "run-1"}

    import requests

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["raw"] = data
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def _body(captured: dict) -> dict:
    import json

    return json.loads(captured["raw"].decode("utf-8"))


def test_a_hafs_run_carries_no_riwayah_field(posted):
    ts_space_client.start_run("some-reciter", chapters=[108], beams=[50, 5])
    assert "riwayah" not in _body(posted)


def test_the_hafs_canonical_preimage_is_unchanged(posted):
    ts_space_client.start_run("some-reciter", chapters=[108], beams=[50, 5])
    assert ts_space_client._canonical(_body(posted)) == (
        '{"beams":[50,5],"chapters":[108],'
        '"profile_id":"timing.timestamps@v1","schema_version":1,'
        '"slug":"some-reciter"}'
    )


def test_a_non_hafs_run_names_its_edition_in_sorted_position(posted):
    ts_space_client.start_run("some-reciter", chapters=[108], riwayah="warsh")
    body = _body(posted)
    assert body["riwayah"] == "warsh"
    # JCS sorts keys, so `riwayah` lands between `profile_id` and
    # `schema_version` — the Space canonicalises the same way or the HMAC fails.
    assert ts_space_client._canonical(body) == (
        '{"chapters":[108],"profile_id":"timing.timestamps@v1",'
        '"riwayah":"warsh","schema_version":1,"slug":"some-reciter"}'
    )


def test_a_float_in_the_body_fails_closed():
    """A schema drift must break the signer, not sign a wrong preimage."""
    with pytest.raises(TypeError):
        ts_space_client._canonical({"beams": [1.5]})


def test_a_paused_space_is_woken_then_the_run_is_retried(monkeypatch):
    monkeypatch.setenv("INSPECTOR_TS_ENGINE_SECRET", "ab" * 32)
    import huggingface_hub
    import requests

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "owner-token")
    responses = [
        SimpleNamespace(status_code=503, text="The space is paused"),
        SimpleNamespace(status_code=200, text="", json=lambda: {"run_id": "run-after-wake"}),
    ]
    posted_nonces: list[str] = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posted_nonces.append(headers["X-Qua-Nonce"])
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    woke: list[str | None] = []
    monkeypatch.setattr(
        ts_space_client,
        "_wake_unavailable_space",
        lambda token: woke.append(token) or True,
    )

    assert ts_space_client.start_run("some-reciter") == "run-after-wake"
    assert woke == ["owner-token"]
    assert len(posted_nonces) == 2
    assert posted_nonces[0] != posted_nonces[1]


def test_a_running_space_503_is_not_retried(monkeypatch):
    monkeypatch.setenv("INSPECTOR_TS_ENGINE_SECRET", "ab" * 32)
    import huggingface_hub
    import requests

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "owner-token")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=503, text="engine unavailable while runtime is running"
        ),
    )
    monkeypatch.setattr(ts_space_client, "_wake_unavailable_space", lambda token: False)

    with pytest.raises(ts_space_client.TsSpaceError, match="engine unavailable"):
        ts_space_client.start_run("some-reciter")


def test_wake_restarts_a_paused_space_and_waits_for_running(monkeypatch):
    stages = iter(["PAUSED", "APP_STARTING", "RUNNING"])
    restarted: list[tuple[str, bool]] = []

    class FakeApi:
        def __init__(self, token):
            assert token == "owner-token"

        def space_info(self, repo_id):
            assert repo_id == ts_space_client.DEFAULT_SPACE_REPO
            return SimpleNamespace(runtime=SimpleNamespace(stage=next(stages)))

        def restart_space(self, *, repo_id, factory_reboot):
            restarted.append((repo_id, factory_reboot))

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setattr(ts_space_client.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(ts_space_client.time, "monotonic", lambda: 0)

    assert ts_space_client._wake_unavailable_space("owner-token") is True
    assert restarted == [(ts_space_client.DEFAULT_SPACE_REPO, False)]
