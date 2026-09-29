"""The private batch timing Space receives the request body and HF bearer token."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.admin import ts_space_client


@pytest.fixture
def posted(monkeypatch):
    import huggingface_hub
    import requests

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "owner-token")
    captured: list[dict] = []

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.append({"url": url, "raw": data, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"run_id": "run-1"})

    monkeypatch.setattr(requests, "post", fake_post)
    return captured


def test_hafs_request_sends_body_and_only_hf_auth(posted):
    assert ts_space_client.start_run("some-reciter", chapters=[108], beams=[50, 5]) == "run-1"
    call = posted[0]
    assert call["url"] == ts_space_client.DEFAULT_SPACE_URL + "/internal/v1/timestamps"
    assert call["timeout"] == 30
    assert json.loads(call["raw"]) == {
        "schema_version": 1,
        "profile_id": "timing.timestamps@v1",
        "slug": "some-reciter",
        "chapters": [108],
        "beams": [50, 5],
    }
    assert call["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer owner-token",
    }


def test_non_hafs_request_names_riwayah(posted):
    ts_space_client.start_run("some-reciter", chapters=[108], riwayah="warsh")
    assert json.loads(posted[0]["raw"])["riwayah"] == "warsh"


def test_missing_hf_token_fails_before_post(monkeypatch):
    import huggingface_hub
    import requests

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("unexpected POST"))
    with pytest.raises(ts_space_client.TsSpaceError, match="HF token"):
        ts_space_client.start_run("some-reciter")


def test_paused_space_is_woken_then_same_request_is_retried(monkeypatch):
    import huggingface_hub
    import requests

    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "owner-token")
    responses = [
        SimpleNamespace(status_code=503, text="The space is paused"),
        SimpleNamespace(status_code=200, text="", json=lambda: {"run_id": "run-after-wake"}),
    ]
    posted: list[tuple[bytes, dict[str, str]]] = []

    def fake_post(url, data=None, headers=None, timeout=None):
        posted.append((data, headers))
        return responses.pop(0)

    monkeypatch.setattr(requests, "post", fake_post)
    woke: list[str] = []
    monkeypatch.setattr(
        ts_space_client,
        "_wake_unavailable_space",
        lambda token: woke.append(token) or True,
    )

    assert ts_space_client.start_run("some-reciter") == "run-after-wake"
    assert woke == ["owner-token"]
    assert len(posted) == 2
    assert posted[0] == posted[1]
    assert posted[0][1] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer owner-token",
    }


def test_running_space_503_is_not_retried(monkeypatch):
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
