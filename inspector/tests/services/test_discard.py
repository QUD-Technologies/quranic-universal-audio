"""Focused tests for the owner-only destructive recitation discard flow."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import huggingface_hub
import pytest

from qua_shared.schemas import Actor, ReciterState, Role
from services.storage import hf_bucket


def test_inspector_runtime_installs_hf_catalog_writer_dependency():
    requirements = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    installed = {
        line.split("#", 1)[0].strip().split(">", 1)[0].split("=", 1)[0]
        for line in requirements.splitlines()
    }

    assert "datasets" in installed


def test_hf_catalog_runtime_preflight_reports_missing_datasets(monkeypatch):
    from services.admin import discard as discard_service

    monkeypatch.setitem(sys.modules, "datasets", None)

    with pytest.raises(discard_service.CleanupFailed, match="datasets package"):
        discard_service._require_hf_catalog_runtime()


@pytest.fixture
def discard_env(tmp_path):
    from services import hf_bucket
    from tests.conftest import _seed_state

    backend = hf_bucket.FilesystemBackend(tmp_path)
    hf_bucket.set_backend(backend)
    _seed_state("test_reciter", state=ReciterState.CATALOGUED.value, reciter_id="rid")
    yield backend
    hf_bucket.reset_backend()


def _owner() -> Actor:
    return Actor(hf_user_id="owner-1", login_at_time="owner", role=Role.OWNER)


def _requester() -> Actor:
    return Actor(hf_user_id="contributor-1", login_at_time="contributor", role=Role.CONTRIBUTOR)


def _release_rows(slug: str) -> None:
    from services import db
    from services.db import repo_releases

    with db.transaction():
        repo_releases.insert_per_recitation_release(
            track="ts",
            slug=slug,
            version="ts-v1",
            produced_at=datetime.now(UTC),
            produced_by="owner-1",
        )
        repo_releases.insert_per_recitation_release(
            track="hf",
            slug=slug,
            version="hf-v1",
            produced_at=datetime.now(UTC),
            produced_by="owner-1",
        )


@pytest.mark.parametrize(
    "state",
    [
        ReciterState.AWAITING_REVIEW.value,
        ReciterState.UNDER_REVIEW.value,
        ReciterState.RELEASED.value,
    ],
)
def test_content_discard_supports_review_and_published_states(state, discard_env, seed_state):
    from services import state as state_service
    from services.db import repo_claims, repo_transitions

    seed_state(
        "test_reciter",
        state=state,
        assignee_hf_id="reviewer-1" if state == ReciterState.UNDER_REVIEW.value else None,
    )
    row = state_service.transition(
        "test_reciter",
        "reciter.content_discarded",
        actor=_owner(),
        reason="owner requested destructive removal",
    )

    assert row.state == ReciterState.CATALOGUED
    assert row.visibility.value == "discarded"
    assert row.assignee_hf_id is None
    assert row.timestamps_job_ids == []
    assert row.revision_in_progress is None
    assert repo_claims.get_open_claim("test_reciter") is None
    assert repo_transitions.for_slug("test_reciter")[-1]["event"] == "reciter.content_discarded"


def test_discard_service_closes_request_and_is_retryable(discard_env, seed_state, monkeypatch):
    from services import db
    from services.admin import discard as discard_service
    from services.db import repo_access, repo_discard_cleanup, repo_requests

    seed_state("test_reciter", state=ReciterState.UNDER_REVIEW.value, assignee_hf_id="reviewer-1")
    _release_rows("test_reciter")
    with db.transaction():
        repo_requests.submit(slug="test_reciter", requester=_requester())

    monkeypatch.setattr(discard_service, "_check_job_locks", lambda slug: None)
    monkeypatch.setattr(discard_service, "_preflight_hf", lambda slug, riwayah: (None, None, []))
    monkeypatch.setattr(discard_service, "_purge_bucket", lambda slug: None)
    monkeypatch.setattr(discard_service, "_purge_hf", lambda **kwargs: 0)

    result = discard_service.discard(
        "test_reciter", actor=_owner(), reason="remove this recording permanently"
    )
    assert result["cleanup_status"] == "completed"
    cleanup = repo_discard_cleanup.get("test_reciter")
    assert cleanup is not None
    assert cleanup["status"] == "completed"
    assert repo_requests.get_pending_row("test_reciter") is None
    assert len(repo_requests.get_for_slug("discarded", "test_reciter")) == 1

    # A cleanup failure leaves the already-hidden delivery retryable. The next
    # call does not emit a second lifecycle transition and can complete it.
    with db.transaction():
        repo_discard_cleanup.mark_failed("test_reciter", "temporary bucket failure")
    result = discard_service.discard(
        "test_reciter", actor=_owner(), reason="retry the recording purge now"
    )
    assert result["cleanup_status"] == "completed"
    cleanup = repo_discard_cleanup.get("test_reciter")
    assert cleanup is not None
    assert cleanup["status"] == "completed"


def test_discard_preflight_failure_does_not_mutate_state(discard_env, seed_state, monkeypatch):
    from services import state as state_service
    from services.admin import discard as discard_service
    from services.db import repo_discard_cleanup

    seed_state("test_reciter", state=ReciterState.AWAITING_REVIEW.value)
    monkeypatch.setattr(discard_service, "_check_job_locks", lambda slug: None)

    def fail_preflight(slug, riwayah):
        raise discard_service.CleanupFailed("datasets runtime unavailable")

    monkeypatch.setattr(discard_service, "_preflight_hf", fail_preflight)

    with pytest.raises(discard_service.CleanupFailed, match="datasets runtime unavailable"):
        discard_service.discard(
            "test_reciter", actor=_owner(), reason="remove this recording permanently"
        )

    row = state_service.get_row("test_reciter")
    assert row is not None
    assert row.state == ReciterState.AWAITING_REVIEW
    assert row.visibility.value == "public"
    assert repo_discard_cleanup.get("test_reciter") is None


def test_undiscard_does_not_restore_content_while_cleanup_is_incomplete(discard_env, seed_state):
    from services import db
    from services import state as state_service
    from services.admin import discard as discard_service
    from services.db import repo_access, repo_discard_cleanup

    seed_state("test_reciter", state=ReciterState.CATALOGUED.value, visibility="discarded")
    with db.transaction():
        repo_access.ensure_user("owner-1", login="owner")
        repo_discard_cleanup.begin(
            slug="test_reciter", requested_by="owner-1", reason="remove content permanently"
        )

    with pytest.raises(discard_service.CleanupPending):
        discard_service.undiscard("test_reciter", actor=_owner(), reason="restore requestability")

    row = state_service.get_row("test_reciter")
    assert row is not None
    assert row.visibility.value == "discarded"


def test_content_discard_rejects_invalid_state_and_non_owner(discard_env, seed_state):
    from services import state as state_service

    seed_state("test_reciter", state=ReciterState.CATALOGUED.value)
    with pytest.raises(state_service.InvalidTransition):
        state_service.transition(
            "test_reciter",
            "reciter.content_discarded",
            actor=_owner(),
            reason="invalid state should not be discarded",
        )

    seed_state("test_reciter", state=ReciterState.AWAITING_REVIEW.value)
    with pytest.raises(state_service.NotAuthorizedForTransition):
        state_service.transition(
            "test_reciter",
            "reciter.content_discarded",
            actor=_requester(),
            reason="only the owner may discard",
        )


def test_discard_route_requires_owner_and_reason(discard_env, seed_state, signed_in_client):
    seed_state("test_reciter", state=ReciterState.AWAITING_REVIEW.value)

    contributor, _ = signed_in_client(hf_user_id="contributor-1", role="contributor")
    response = contributor.post(
        "/api/admin/reciter/test_reciter/discard",
        json={"reason": "long enough reason"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 403

    owner, _ = signed_in_client(hf_user_id="owner-1", login="owner", role="owner")
    response = owner.post(
        "/api/admin/reciter/test_reciter/discard",
        json={"reason": "short"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 400


def test_recursive_storage_purge_is_exact_and_verified(tmp_path):
    from services.storage.hf_bucket import FilesystemBackend, delete_tree_verified

    backend = FilesystemBackend(tmp_path)
    backend.write_bytes_atomic("reciters/alpha/audio/a.mp3", b"a")
    backend.write_bytes_atomic("reciters/alpha/timestamps/1.json.br", b"t")
    backend.write_bytes_atomic("reciters/alphabet/audio/a.mp3", b"keep")

    delete_tree_verified(backend, "reciters/alpha")

    assert not backend.exists("reciters/alpha")
    assert backend.exists("reciters/alphabet/audio/a.mp3")


def test_discard_purge_preserves_audio_manifest(tmp_path, monkeypatch):
    from services.admin import discard as discard_service
    from services.storage.hf_bucket import FilesystemBackend

    backend = FilesystemBackend(tmp_path)
    backend.write_bytes_atomic("reciters/alpha/audio/a.mp3", b"a")
    backend.write_bytes_atomic("catalog/audio_manifest/alpha.json", b'{"chapters": {"1": {}}}')
    monkeypatch.setattr(discard_service, "get_backend", lambda: backend)

    discard_service._purge_bucket("alpha")

    assert not backend.exists("reciters/alpha")
    assert backend.exists("catalog/audio_manifest/alpha.json")


def test_mount_purge_finishes_when_an_emptied_dir_vanishes_first(tmp_path, monkeypatch):
    """hf-mount drops a directory once its last file is deleted, so the rmtree
    that follows found nothing and the whole discard failed on
    ``reciters/<slug>/timing`` — before it reached the files after it."""
    reciter = tmp_path / "reciters" / "alpha"
    (reciter / "timing").mkdir(parents=True)
    (reciter / "timing" / "1.json").write_bytes(b"t")
    (reciter / "verse_ends_v1.json").write_bytes(b"v")
    real_rmtree = hf_bucket.shutil.rmtree

    def mount_rmtree(path, *args, **kwargs):
        real_rmtree(path)
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(hf_bucket.shutil, "rmtree", mount_rmtree)
    monkeypatch.setattr(huggingface_hub, "batch_bucket_files", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(huggingface_hub, "list_bucket_tree", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(hf_bucket.BucketBackend, "_is_not_found", staticmethod(lambda e: True))
    backend = hf_bucket.BucketBackend("o/b", token=None, mount=tmp_path)

    hf_bucket.delete_tree_verified(backend, "reciters/alpha")

    assert not reciter.exists()
