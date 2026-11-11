"""Cut jobs snapshot the saved release contents setting into the HF job env."""

from types import SimpleNamespace

import pytest

from qua_shared.schemas import AutomationConfig, GhCutConfig
from services.admin.automation import config
from services.admin.jobs import cut_release


@pytest.mark.parametrize("include_letters", [True, False])
def test_launch_uses_saved_letter_setting(monkeypatch, include_letters):
    import huggingface_hub

    monkeypatch.setattr(
        config,
        "load_config",
        lambda: AutomationConfig(gh_cut=GhCutConfig(include_letter_timestamps=include_letters)),
    )
    monkeypatch.setattr(cut_release.base, "running_job_for", lambda **kw: None)
    monkeypatch.setattr(cut_release.base, "stage_job_code", lambda: None)
    monkeypatch.setattr(cut_release, "resolve_bucket_repo", lambda: "owner/bucket")
    monkeypatch.setattr(cut_release.records, "record_launch", lambda *a, **kw: None)
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: "test-token")
    launches = []

    def run_job(**kwargs):
        launches.append(kwargs)
        return SimpleNamespace(id="test-job", url="https://example.com/job")

    monkeypatch.setattr(huggingface_hub, "run_job", run_job)
    cut_release.launch()
    assert launches[0]["env"]["INCLUDE_LETTER_TIMESTAMPS"] == ("1" if include_letters else "0")
