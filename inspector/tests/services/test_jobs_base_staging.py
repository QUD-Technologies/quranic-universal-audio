"""Tests for ``services.admin.jobs.base.stage_job_code``.

Pins the "every required entrypoint and static ref is COPYed into the runtime
image" contract: when a path is missing on disk, ``stage_job_code`` must raise
``JobStagingError`` with a clear hint BEFORE calling ``run_job``. The
HF-Job-side ``No such file or directory`` failure surfaced from a Dockerfile
COPY drop is exactly what this guards against.
"""

from __future__ import annotations

import sys
import types

import pytest

from services.admin.jobs import base


@pytest.fixture
def stub_batch(monkeypatch):
    """Stub ``batch_bucket_files`` so we can assert on the upload arg without
    touching HF infra. Snapshots the bytes of every source file at call time
    (auto-gzipped temp sources are deleted in stage_job_code's finally block,
    so reading them post-call would race the cleanup)."""
    calls: list[dict] = []
    mod = sys.modules.get("huggingface_hub")
    if mod is None:
        mod = types.ModuleType("huggingface_hub")
        monkeypatch.setitem(sys.modules, "huggingface_hub", mod)

    def fake(bucket, *, add):
        snapshot = []
        for src, target in add:
            try:
                blob = open(src, "rb").read()
            except OSError:
                blob = None
            snapshot.append((src, target, blob))
        calls.append({"bucket": bucket, "add": list(add), "snapshot": snapshot})

    monkeypatch.setattr(mod, "batch_bucket_files", fake, raising=False)
    return calls


def _write_minimal_tree(tmp_path):
    """A synthetic, hermetic repo tree satisfying every required path."""
    (tmp_path / "qua_shared").mkdir(parents=True)
    (tmp_path / "qua_shared" / "__init__.py").write_text("")
    (tmp_path / "qua_jobs").mkdir(parents=True)
    for ep in base.REQUIRED_ENTRYPOINTS:
        (tmp_path / ep).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ep).write_text("# stub")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "surah_info.json").write_text("{}")
    (tmp_path / "data" / "digital_khatt_v2_script.json").write_text("{}")
    (tmp_path / "inspector" / "frontend" / "public" / "fonts").mkdir(parents=True)
    (tmp_path / "inspector" / "frontend" / "public" / "fonts" / "DigitalKhattV2.otf").write_bytes(
        b"font"
    )
    (tmp_path / ".github" / "config").mkdir(parents=True)
    (tmp_path / ".github" / "config" / "repo.yml").write_text("hf_dataset: foo/bar")
    (tmp_path / "docs" / "templates").mkdir(parents=True)
    (tmp_path / "docs" / "templates" / "release_body.md").write_text("{{ release_title }}")
    (tmp_path / "docs" / "templates" / "hf_dataset_card.md").write_text("{{ dataset_title }}")
    (tmp_path / "LICENSE").write_text("MIT")


def test_stage_job_code_uploads_every_required_path(stub_batch, monkeypatch, tmp_path):
    """Happy path against a synthetic, hermetic repo tree — every required
    entrypoint AND every required static file must be in the upload manifest.

    Mirrors the other tests' tmp_path + REPO_ROOT monkeypatch isolation so
    the assertion no longer depends on the real ``qua_shared`` / ``qua_jobs``
    trees on disk.
    """
    _write_minimal_tree(tmp_path)
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(base, "QUA_DOMAIN_WHEEL_DIR", tmp_path / "no-wheels")

    base.stage_job_code()
    assert len(stub_batch) == 1
    targets = {target for _src, target in stub_batch[0]["add"]}
    for rel in base.REQUIRED_ENTRYPOINTS:
        assert f"code/{rel}" in targets, f"missing entrypoint upload: {rel}"
    for rel in base.REQUIRED_STATIC_FILES:
        assert f"code/{rel}" in targets, f"missing static upload: {rel}"


def test_stage_job_code_ships_qua_domain_wheel(stub_batch, monkeypatch, tmp_path):
    """The image's qua_domain wheel rides along under code/wheels/ so a
    non-Hafs publish/cut projects into the riwayah's coordinates; without a
    wheel the upload still goes (Hafs-only jobs)."""
    _write_minimal_tree(tmp_path)
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)
    wheels = tmp_path / "wheels"
    monkeypatch.setattr(base, "QUA_DOMAIN_WHEEL_DIR", wheels)

    base.stage_job_code()
    assert not any(t.startswith("code/wheels/") for _s, t in stub_batch[-1]["add"])

    wheels.mkdir()
    (wheels / "qua_domain-0.1.0-py3-none-any.whl").write_bytes(b"PK")
    base.stage_job_code()
    targets = {t for _s, t in stub_batch[-1]["add"]}
    assert "code/wheels/qua_domain-0.1.0-py3-none-any.whl" in targets


def test_runtime_image_copies_digital_khatt_release_assets():
    dockerfile = (base.REPO_ROOT / "inspector" / "Dockerfile").read_text()
    assert "data/digital_khatt_v2_script.json" in dockerfile
    assert "COPY --chmod=0644 inspector/frontend/public/fonts/DigitalKhattV2.otf.gz" in dockerfile


def test_stage_job_code_requires_digital_khatt_assets(stub_batch, monkeypatch, tmp_path):
    """The v13 public projection cannot launch without its exact script and font."""
    (tmp_path / "qua_shared").mkdir(parents=True)
    (tmp_path / "qua_jobs").mkdir(parents=True)
    for ep in base.REQUIRED_ENTRYPOINTS:
        (tmp_path / ep).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ep).write_text("# stub")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "surah_info.json").write_text("{}")
    (tmp_path / ".github" / "config").mkdir(parents=True)
    (tmp_path / ".github" / "config" / "repo.yml").write_text("hf_dataset: foo/bar")
    (tmp_path / "docs" / "templates").mkdir(parents=True)
    (tmp_path / "docs" / "templates" / "release_body.md").write_text("{{ release_title }}")
    (tmp_path / "docs" / "templates" / "hf_dataset_card.md").write_text("{{ dataset_title }}")
    (tmp_path / "LICENSE").write_text("MIT")
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)

    with pytest.raises(base.JobStagingError) as exc:
        base.stage_job_code()

    message = str(exc.value)
    assert "data/digital_khatt_v2_script.json" in message
    assert "DigitalKhattV2.otf" in message
    assert stub_batch == []


def test_runtime_dockerfile_validates_release_font_bytes():
    dockerfile = (base.REPO_ROOT / "inspector" / "Dockerfile").read_text(encoding="utf-8")

    assert "public/fonts/DigitalKhattV2.otf.gz.b64" in dockerfile
    assert "base64 -d | gzip -dc > public/fonts/DigitalKhattV2.otf" in dockerfile
    assert (
        "base64 -d | gzip -dc > ./inspector/frontend/public/fonts/DigitalKhattV2.otf" in dockerfile
    )
    assert '[ "$(head -c 4 public/fonts/DigitalKhattV2.otf)" = "OTTO" ]' in dockerfile
    assert "test -s ./inspector/frontend/public/fonts/DigitalKhattV2.otf" in dockerfile
    assert 'grep -q "version https://git-lfs"' in dockerfile
    assert "chmod -R a+rX ./inspector/frontend" in dockerfile


def test_stage_job_code_raises_when_entrypoint_missing(stub_batch, monkeypatch, tmp_path):
    """Point REPO_ROOT at a tree missing qua_jobs/publish_hf.py; expect
    ``JobStagingError`` with the missing path in the message."""
    (tmp_path / "qua_shared").mkdir(parents=True)
    (tmp_path / "qua_jobs").mkdir(parents=True)
    # Only generate_timestamps.py exists — publish_hf/cut_release/shard absent.
    (tmp_path / "qua_jobs" / "generate_timestamps.py").write_text("# stub")
    (tmp_path / "qua_shared" / "__init__.py").write_text("")
    monkeypatch.setattr(base, "REPO_ROOT", tmp_path)

    with pytest.raises(base.JobStagingError) as exc:
        base.stage_job_code()
    msg = str(exc.value)
    assert "qua_jobs/publish_hf.py" in msg
    assert "Dockerfile" in msg
    assert stub_batch == [], "must not upload when contract is broken"


# --- poll-handler registration (the auto-regen completion path) --------------


def test_register_poll_handlers_includes_timestamps(monkeypatch):
    """Every poll-completable kind — crucially the TS handler — must be
    registered under the kind the HF job is labelled with (``timestamps``).

    The TS handler being absent (or, historically, registered under the wrong
    key "ts" while the job is labelled "timestamps") left automated TS regens
    with no completion path (no webhook, no open drawer, poll skips a label it
    has no handler for), freezing ``produced_at`` and looping the auto-regen
    automation. The handler set is asserted against the single source of truth
    so any future omission fails here instead of in prod."""
    saved = dict(base._HANDLERS)
    base._HANDLERS.clear()
    try:
        base.register_poll_handlers()
        assert set(base._HANDLERS) == set(base.POLL_COMPLETABLE_KINDS)
        assert "timestamps" in base._HANDLERS
        # cut_release is webhook-only — it must NOT be on the poll path.
        assert "cut_release" not in base._HANDLERS
    finally:
        base._HANDLERS.clear()
        base._HANDLERS.update(saved)


def test_poll_dispatches_timestamps_labelled_job_to_handler(monkeypatch):
    """A completed HF job labelled ``task='timestamps'`` must reach the TS
    handler. The dispatcher keys handlers by the raw HF label, so a handler
    registered under any other string (the historical "ts") is silently skipped
    and the reciter never releases — this drives the real label→handler path
    that ``test_register_poll_handlers_includes_timestamps`` alone can't prove."""
    hub = sys.modules.get("huggingface_hub")
    if hub is None:
        hub = types.ModuleType("huggingface_hub")
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    fake_job = types.SimpleNamespace(
        id="jid-ts-1",
        labels={"task": "timestamps", "reciter": "foo_slug"},
        status=types.SimpleNamespace(stage="COMPLETED"),
    )
    monkeypatch.setattr(hub, "list_jobs", lambda: [fake_job], raising=False)

    dispatched: list[tuple[str | None, str]] = []
    saved = dict(base._HANDLERS)
    base._HANDLERS.clear()
    base._completed_jobs.clear()
    try:
        base.register_handler("timestamps", lambda slug, jid: dispatched.append((slug, jid)))
        base._poll_terminal_jobs()
        assert dispatched == [("foo_slug", "jid-ts-1")]
    finally:
        base._HANDLERS.clear()
        base._HANDLERS.update(saved)
        base._completed_jobs.clear()


def test_poll_skips_already_completed_job_on_second_tick(monkeypatch):
    """A terminal job HF still lists on the next tick is NOT re-dispatched once
    its handler has run — this is what stops the idempotent completion handlers
    from re-pushing the whole DB to the bucket every 120s poll cycle."""
    hub = sys.modules.get("huggingface_hub")
    if hub is None:
        hub = types.ModuleType("huggingface_hub")
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    fake_job = types.SimpleNamespace(
        id="jid-hf-1",
        labels={"task": "hf_publish", "reciter": "foo_slug"},
        status=types.SimpleNamespace(stage="COMPLETED"),
    )
    monkeypatch.setattr(hub, "list_jobs", lambda: [fake_job], raising=False)

    dispatched: list[tuple[str | None, str]] = []
    saved = dict(base._HANDLERS)
    base._HANDLERS.clear()
    base._completed_jobs.clear()
    try:
        base.register_handler("hf_publish", lambda slug, jid: dispatched.append((slug, jid)))
        base._poll_terminal_jobs()
        base._poll_terminal_jobs()
        assert dispatched == [("foo_slug", "jid-hf-1")]
    finally:
        base._HANDLERS.clear()
        base._HANDLERS.update(saved)
        base._completed_jobs.clear()


def test_poll_dispatches_terminal_timestamp_space_run(monkeypatch):
    """A terminal bucket record completes without an open admin job drawer."""
    from services.admin import timestamps_jobs

    hub = sys.modules.get("huggingface_hub")
    if hub is None:
        hub = types.ModuleType("huggingface_hub")
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(hub, "list_jobs", lambda: [], raising=False)
    monkeypatch.setattr(
        timestamps_jobs,
        "terminal_success_runs",
        lambda: [("foo_slug", "space-run-1")],
    )

    dispatched: list[tuple[str | None, str]] = []
    saved = dict(base._HANDLERS)
    base._HANDLERS.clear()
    base._completed_jobs.clear()
    try:
        base.register_handler("timestamps", lambda slug, jid: dispatched.append((slug, jid)))
        base._poll_terminal_jobs()
        base._poll_terminal_jobs()
        assert dispatched == [("foo_slug", "space-run-1")]
    finally:
        base._HANDLERS.clear()
        base._HANDLERS.update(saved)
        base._completed_jobs.clear()


def test_poll_re_fires_handler_that_raised(monkeypatch):
    """A handler that raises is NOT memoized, so the next tick retries it — the
    memo only suppresses re-fires after a clean completion."""
    hub = sys.modules.get("huggingface_hub")
    if hub is None:
        hub = types.ModuleType("huggingface_hub")
        monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    fake_job = types.SimpleNamespace(
        id="jid-hf-2",
        labels={"task": "hf_publish", "reciter": "foo_slug"},
        status=types.SimpleNamespace(stage="COMPLETED"),
    )
    monkeypatch.setattr(hub, "list_jobs", lambda: [fake_job], raising=False)

    calls = {"n": 0}

    def _boom(slug, jid):
        calls["n"] += 1
        raise RuntimeError("handler failed")

    saved = dict(base._HANDLERS)
    base._HANDLERS.clear()
    base._completed_jobs.clear()
    try:
        base.register_handler("hf_publish", _boom)
        base._poll_terminal_jobs()
        base._poll_terminal_jobs()
        assert calls["n"] == 2
    finally:
        base._HANDLERS.clear()
        base._HANDLERS.update(saved)
        base._completed_jobs.clear()
