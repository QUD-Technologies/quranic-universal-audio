"""A committed lifecycle transition drops the TS manifest's process cache.

The manifest (services/reference/timestamps.py) is cached behind a ``_built``
flag and a ``_built_seq`` db_seq snapshot. The db_seq check is the authoritative
rebuild trigger — any committed write bumps db_seq and the next manifest read
self-heals. The post-commit ``invalidate()`` call from ``state.transition()``
is a belt-and-braces explicit drop that keeps the hot path off the seq check.
These tests pin both halves so neither can silently regress.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

from qua_shared.schemas import Actor, AudioCategory, Delivery, ReciterEntry, Role
from services.reference import timestamps as ts_manifest
from services.state import state as state_service

_OWNER = Actor(hf_user_id="owner-1", login_at_time="owner", role=Role.OWNER)


@pytest.fixture(autouse=True)
def _reset_ts_manifest_cache():
    """Clear the process-wide TS manifest cache around every test.

    The autouse ``_substrate_db`` fixture resets the DB but not the manifest's
    ``_built`` / ``_built_seq`` / ``_served_slugs`` module state. Without this
    drop, a warmed cache from a previous test leaks into the next case.
    """
    ts_manifest.invalidate()
    yield
    ts_manifest.invalidate()


def _seed_marked_ready(slug: str = "rec_a") -> None:
    """Seed an under_review reciter with an open, marked-ready claim — the
    pre-publish state the timestamps job runs against."""
    from tests.conftest import _seed_catalog, _seed_state

    _seed_catalog(
        reciters=[ReciterEntry(reciter_id=slug, name_en="Reciter A")],
        deliveries=[
            Delivery(
                slug=slug,
                reciter_id=slug,
                riwayah="hafs",
                style="mur",
                source="src",
                channel="ch",
                audio_category=AudioCategory.BY_SURAH,
                chapter_count=114,
                added_at=datetime.now(UTC),
                added_by_hf_id="seed",
            )
        ],
    )
    _seed_state(slug, state="under_review", assignee_hf_id="u-rev", marked_ready=True)


def test_transition_drops_manifest_cache():
    _seed_marked_ready("rec_a")
    ts_manifest.manifest_bytes()  # warm the process cache
    assert ts_manifest._built is True

    state_service.transition(
        "rec_a",
        "reciter.published",
        actor=_OWNER,
        payload={"job_id": "job-1"},
    )

    # Cache dropped → next manifest request rebuilds with the new published set.
    assert ts_manifest._built is False


def test_transition_invokes_invalidate_hook(monkeypatch):
    """The drop goes through ``timestamps.invalidate()`` (the lazy import in
    ``state.transition`` resolves to the same module object we patch here)."""
    _seed_marked_ready("rec_a")
    calls: list[int] = []
    real = ts_manifest.invalidate
    monkeypatch.setattr(ts_manifest, "invalidate", lambda: (calls.append(1), real())[1])

    state_service.transition(
        "rec_a",
        "reciter.published",
        actor=_OWNER,
        payload={"job_id": "job-1"},
    )

    assert calls == [1]


def test_manifest_self_heals_on_db_seq_without_explicit_invalidate(monkeypatch):
    """The db_seq check is the AUTHORITATIVE rebuild trigger — even if the
    explicit ``invalidate()`` never fires (it sits after the durable upload in
    ``state.transition`` and is skipped if that step raises), a committed state
    change bumps ``db_seq`` so the next manifest request rebuilds. This is the
    fix for "released reciter missing from the TS tab until a Space restart"."""
    _seed_marked_ready("rec_a")  # under_review + marked_ready + catalog(114/by_surah)
    ts_manifest.manifest_bytes()  # warm
    assert "rec_a" not in ts_manifest._served_slugs  # under_review → not served

    # Neuter the explicit drop so ONLY the db_seq check can refresh the cache.
    monkeypatch.setattr(ts_manifest, "invalidate", lambda: None)

    state_service.transition(
        "rec_a",
        "reciter.published",
        actor=_OWNER,
        payload={"job_id": "job-1"},
    )
    # _built is still True (invalidate was a no-op), but db_seq advanced →
    # _ensure_built rebuilds on the next request and now serves the released slug.
    ts_manifest.manifest_bytes()
    assert "rec_a" in ts_manifest._served_slugs


def test_manifest_cache_hit_when_db_seq_unchanged(monkeypatch):
    """No rebuild when the DB hasn't advanced — the db_seq guard is a cache
    HIT, not a forced rebuild on every request."""
    _seed_marked_ready("rec_a")
    ts_manifest.manifest_bytes()  # warm + set _built_seq
    calls: list[int] = []
    real_build = ts_manifest._build_manifest_dict
    monkeypatch.setattr(
        ts_manifest,
        "_build_manifest_dict",
        lambda block: (calls.append(1), real_build(block))[1],
    )
    ts_manifest.manifest_bytes()  # same db_seq → no rebuild
    ts_manifest.manifest_bytes()
    assert calls == []


def test_db_seq_rebuild_reuses_static_resources(monkeypatch):
    """A rebuild on a ``db_seq`` bump re-reads state, not the static fonts/scripts."""
    from services import db

    built = []
    monkeypatch.setattr(
        ts_manifest, "_build_resource_bytes", lambda: built.append(1) or {"r": b"x"}
    )
    seq = iter(range(1, 100))
    monkeypatch.setattr(db, "current_db_seq", lambda *a: next(seq))
    ts_manifest.manifest_bytes()
    first = ts_manifest._built_seq
    ts_manifest.manifest_bytes()
    assert ts_manifest._built_seq != first
    assert built == [1]
