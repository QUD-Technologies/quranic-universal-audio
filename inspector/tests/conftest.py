"""Shared pytest fixtures for the inspector test suite.

The Flask app in ``inspector/app.py`` is constructed at module import time
(module-level ``app = Flask(...)``), not via a factory. Tests that need a
client spin one up via ``app.test_client()``.

This module also provides the helpers used by the segments-tab refactor
test suite:

- ``load_fixture`` reads a JSON fixture from
  ``inspector/tests/fixtures/segments/<name>.detailed.json``.
- ``load_expected`` reads a baseline from
  ``inspector/tests/fixtures/segments/expected/<name>.<kind>.json``.
- ``flask_client`` exposes the Flask test client.
- ``tmp_reciter_dir`` redirects the data path to a writable per-test
  directory and exposes an ``install`` helper to drop a fixture into
  ``<reciter>/detailed.json``.
- ``ALL_CATEGORIES`` enumerates the 11 validation categories. After
  Phase 1 lands the registry, this constant flips to read from
  ``inspector.services.validation.registry.IssueRegistry.keys()``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# Seed the session-signing secret BEFORE any test module or fixture imports
# inspector code that touches ``flask.session`` or signs cookies. ``app.py``
# tolerates a missing secret (warns + 503s OAuth routes) but anything that
# signs a cookie inside a test needs a value. Centralising the setdefault here
# means individual test files don't have to repeat the bootstrap.
os.environ.setdefault("INSPECTOR_SESSION_SECRET", "0" * 64)

import pytest  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "segments"
EXPECTED_DIR = FIXTURES_DIR / "expected"


# Category lists derive from ``services.validation.registry.IssueRegistry``
# in registry-declared accordion order (Appendix A). Imported directly so
# the suite fails loudly if the registry module can't be imported.
from datetime import UTC

from services.validation.registry import (  # noqa: E402
    ALL_CATEGORIES as _REG_ALL,
)
from services.validation.registry import (
    AUTO_SUPPRESS_CATEGORIES as _REG_AUTO,
)
from services.validation.registry import (
    CAN_IGNORE_CATEGORIES as _REG_CAN,
)
from services.validation.registry import (
    PER_CHAPTER_CATEGORIES as _REG_CHAPTER,
)
from services.validation.registry import (
    PER_SEGMENT_CATEGORIES as _REG_SEG,
)
from services.validation.registry import (
    PER_VERSE_CATEGORIES as _REG_VERSE,
)
from services.validation.registry import (
    PERSISTS_IGNORE_CATEGORIES as _REG_PERSIST,
)

ALL_CATEGORIES = list(_REG_ALL)
PER_SEGMENT_CATEGORIES = list(_REG_SEG)
PER_VERSE_CATEGORIES = list(_REG_VERSE)
PER_CHAPTER_CATEGORIES = list(_REG_CHAPTER)
CAN_IGNORE_CATEGORIES = list(_REG_CAN)
PERSISTS_IGNORE_CATEGORIES = list(_REG_PERSIST)
AUTO_SUPPRESS_CATEGORIES = list(_REG_AUTO)


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def load_fixture():
    """Return a function that loads ``<name>.detailed.json`` from the fixtures dir."""

    def _loader(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.detailed.json"
        return _read_json(path)

    return _loader


@pytest.fixture
def load_expected():
    """Return a function that loads ``expected/<name>.<kind>.json``."""

    def _loader(name: str, kind: str) -> dict:
        path = EXPECTED_DIR / f"{name}.{kind}.json"
        return _read_json(path)

    return _loader


@pytest.fixture
def flask_client():
    """Flask test client over the module-level app in ``inspector/app.py``."""
    from app import app

    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# SQLite substrate test harness (post-cutover: the DB is the source of truth).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _substrate_db(tmp_path):
    """Every test runs against a fresh, migrated SQLite DB.

    Bucket uploads are disabled (``set_sync_enabled(False)``) so the bulk of the
    suite doesn't pay snapshot/CAS cost; durability tests flip it back on. The
    DB file lives under the same ``tmp_path`` the content fixtures use, so the
    SQLite substrate and the per-reciter ``reciters/<slug>/`` FilesystemBackend
    content compose in one temp dir.
    """
    from services import db
    from services.db import sync as _sync
    from services.storage import cache as _cache

    db.set_db_path_for_test(tmp_path / "inspector-test.db")
    db.init_db()
    _sync.set_sync_enabled(False)
    _sync._reset_for_test()
    # The public-reciters + catalog-snapshot caches are keyed on db_seq;
    # db.reset() restarts that counter, so clear them between tests to avoid
    # cross-test bleed.
    _cache.invalidate_public_reciters_cache()
    _cache.invalidate_catalog_snapshot_cache()
    _cache.invalidate_admin_users_cache()
    _cache.invalidate_admin_requests_cache()
    _cache.invalidate_capability_matrix_cache()
    yield
    db.reset()
    _sync.set_sync_enabled(True)
    _cache.invalidate_public_reciters_cache()
    _cache.invalidate_catalog_snapshot_cache()
    _cache.invalidate_admin_users_cache()
    _cache.invalidate_admin_requests_cache()
    _cache.invalidate_capability_matrix_cache()


def _seed_delivery_chain(conn, slug: str, reciter_id: str = "r") -> None:
    """Insert the FK chain a ``delivery_states`` row needs: vocab + reciter +
    delivery (all idempotent)."""
    conn.execute(
        "INSERT OR IGNORE INTO reciters(reciter_id,name_en) VALUES (?,?)",
        (reciter_id, reciter_id.upper()),
    )
    conn.execute("INSERT OR IGNORE INTO riwayahs(slug,short,name) VALUES ('hafs','h','Hafs')")
    conn.execute("INSERT OR IGNORE INTO styles(slug,short,name) VALUES ('mur','m','Mur')")
    conn.execute("INSERT OR IGNORE INTO sources(slug,name) VALUES ('src','Src')")
    conn.execute("INSERT OR IGNORE INTO channels(slug,short,name) VALUES ('ch','c','Ch')")
    conn.execute(
        "INSERT OR IGNORE INTO deliveries(slug,reciter_id,riwayah,style,source,channel,"
        "audio_category,chapter_count,added_at,added_by_hf_id) VALUES "
        "(?,?,'hafs','mur','src','ch','by_surah',114,'2026-01-01T00:00:00Z','sys')",
        (slug, reciter_id),
    )


def _seed_state(
    slug: str,
    *,
    state: str = "awaiting_review",
    state_since=None,
    visibility: str = "public",
    visibility_reason: str | None = None,
    assignee_hf_id: str | None = None,
    assignee_login: str = "test_user",
    marked_ready: bool = False,
    last_save_at=None,
    reciter_id: str = "rid",
    timestamps_job_ids=None,
    revision_in_progress=None,
) -> None:
    """Seed a ``delivery_states`` row (+ FK chain), synthesizing an open
    ``claims`` row when ``assignee_hf_id`` is given. Backs the legacy per-file
    ``_row(...)`` / ``_replace_state(...)`` seeding. ``assignee_*``/``marked_ready``
    only surface on UNDER_REVIEW rows (the ReciterRow invariant), so pass
    ``state="under_review"`` with an assignee."""
    from datetime import datetime, timezone

    from qua_shared.schemas import ReciterState, Visibility
    from services import db
    from services.db import repo_access, repo_claims, repo_state

    now = datetime.now(UTC)
    st = state if isinstance(state, ReciterState) else ReciterState(state)
    vis = visibility if isinstance(visibility, Visibility) else Visibility(visibility)
    # Schema CHECK: discarded ⇒ visibility_reason NOT NULL.
    if vis == Visibility.DISCARDED and not visibility_reason:
        visibility_reason = "seeded discarded"
    with db.transaction() as conn:
        # Only seed the default FK chain (vocab+reciter+delivery) when the
        # delivery doesn't already exist — tests that pre-insert a custom
        # catalog must not get the default vocab merged in.
        if conn.execute("SELECT 1 FROM deliveries WHERE slug = ?", (slug,)).fetchone() is None:
            _seed_delivery_chain(conn, slug, reciter_id)
        repo_state.upsert_state(
            slug,
            state=st,
            state_since=state_since or now,
            visibility=vis,
            visibility_reason=visibility_reason,
            last_save_at=last_save_at,
            timestamps_job_ids=list(timestamps_job_ids or []),
            revision_in_progress=revision_in_progress,
        )
        if assignee_hf_id is not None:
            repo_access.ensure_user(assignee_hf_id, login=assignee_login)
            repo_claims.open_claim(
                slug=slug,
                assignee_id=assignee_hf_id,
                assignee_login=assignee_login,
                claimed_at=now,
            )
            if marked_ready:
                repo_claims.set_marked_ready(slug, ready=True)


def _seed_catalog(*, reciters=(), deliveries=(), vocab=None) -> None:
    """Seed catalog rows (vocab + reciters + deliveries) into the substrate.

    Default vocab covers the ``_seed_state`` delivery defaults
    (hafs/mur/src/ch). Pass a custom ``Vocab`` when deliveries reference other
    slugs."""
    from qua_shared.schemas import Channel, Riwayah, Source, Style, Vocab
    from services import db
    from services.db import repo_catalog

    if vocab is None:
        vocab = Vocab(
            riwayat=[Riwayah(slug="hafs", short="h", name="Hafs")],
            styles=[Style(slug="mur", short="m", name="Mur")],
            sources=[Source(slug="src", name="Src")],
            channels=[Channel(slug="ch", short="c", name="Ch")],
            recording_contexts=[],
        )
    with db.transaction():
        repo_catalog.load_vocab(vocab)
        for r in reciters:
            repo_catalog.insert_reciter(r)
        for d in deliveries:
            repo_catalog.insert_delivery(d)


def _seed_role(hf_user_id: str, *, login: str = "test_user", role: str = "contributor") -> None:
    """Seed a member's role (CONTRIBUTOR is implicit → just ensure the user row
    for FK targets)."""
    from qua_shared.schemas import Role
    from services import db
    from services.db import repo_access

    with db.transaction():
        repo_access.ensure_user(hf_user_id, login=login)
        if role != "contributor":
            repo_access.grant_role(
                hf_user_id=hf_user_id,
                login=login,
                role=Role(role),
                granted_by="test-seed",
            )


@pytest.fixture
def seed_state():
    """Callable: ``seed_state(slug, state=, assignee_hf_id=, marked_ready=, ...)``."""
    return _seed_state


@pytest.fixture
def seed_role():
    """Callable: ``seed_role(hf_user_id, login=, role=)``."""
    return _seed_role


@pytest.fixture
def signed_in_client(monkeypatch):
    """Factory returning a Flask test client that carries a signed identity cookie.

    Usage::

        def test_x(signed_in_client):
            client, user = signed_in_client(role="contributor")
            resp = client.post("/api/claim/foo", headers={"Origin": "http://localhost"})

    The fixture:
    - Ensures ``INSPECTOR_SESSION_SECRET`` is set (auto-seeded if absent).
    - Seeds the ``access`` store with the requested role for the user.
    - Mints a signed cookie via ``auth_service.encode_session`` and attaches
      it to the test client.
    - Returns ``(client, user_dict)`` where ``user_dict`` carries
      ``{hf_user_id, login, role}`` for assertions in the test.
    """
    import secrets

    if not os.environ.get("INSPECTOR_SESSION_SECRET"):
        monkeypatch.setenv("INSPECTOR_SESSION_SECRET", secrets.token_hex(32))

    from app import app

    from services import auth as auth_service

    app.config["TESTING"] = True

    def _make(
        *,
        hf_user_id: str = "test-user-1",
        login: str = "test_user",
        role: str = "contributor",
    ):
        # Seed the role into the SQLite substrate (CONTRIBUTOR is implicit;
        # resolve_role returns CONTRIBUTOR for unknown ids). The autouse
        # _substrate_db fixture handles teardown.
        _seed_role(hf_user_id, login=login, role=role)

        client = app.test_client()
        cookie = auth_service.encode_session(login=login, hf_user_id=hf_user_id)
        client.set_cookie(
            auth_service.SESSION_COOKIE_NAME,
            cookie,
            path="/",
        )
        return client, {"hf_user_id": hf_user_id, "login": login, "role": role}

    yield _make


@pytest.fixture
def state_persistence(tmp_path, monkeypatch):
    """Per-test FilesystemBackend for per-reciter content + the SQLite substrate
    (via the autouse ``_substrate_db`` fixture) for state.

    Post-cutover, state persists across requests for free (it lives in SQLite).
    This fixture just wires the FilesystemBackend for any ``reciters/<slug>/`` content
    a test reads, and yields it for inspection. Seed state via the ``seed_state``
    fixture / ``_seed_state`` helper, or by driving ``transition()``.
    """
    from services import hf_bucket as _hf_bucket

    monkeypatch.setenv("INSPECTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INSPECTOR_BACKEND", "filesystem")
    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))

    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)

    yield backend

    _hf_bucket.reset_backend()


_SEG_CACHE_NAMES = (
    "_seg",
    "_seg_meta",
    "_seg_verses",
    "_seg_resolved_by_edit",
    "_seg_probe_v2",
    "_seg_auto_split",
    "_seg_hidden_pause",
    "_seg_missed_waqf",
    "_seg_false_split",
    "_seg_unmarked_wasl",
    "_seg_pipeline_meta",
    "_seg_history_batches",
    "_seg_split_group_index",
    "_seg_edit_history",
    "_seg_history_peaks",
    "_seg_history_peaks_response",
    "_seg_validate_result",
    "_seg_stats_result",
)


def _invalidate_seg_caches(reciter: str | None = None):
    """Invalidate every per-reciter segment cache so a previous test's slug
    can't leak parsed JSONL / derived indices into the next test.

    The new edit-history-derived caches (``_seg_history_batches``,
    ``_seg_split_group_index``) are NOT touched by the production save/undo
    invalidation (they're append-on-save by design) — but in tests we want
    full eviction between cases because tests reuse the ``fixture_reciter``
    slug across distinct ``tmp_path``s.
    """
    try:
        from services import cache as _cache
    except Exception:
        return
    for name in _SEG_CACHE_NAMES:
        obj = getattr(_cache, name, None)
        if obj is None:
            continue
        if hasattr(obj, "clear"):
            obj.clear()
    # Reset the route-level peaks LRU response cache so successive tests
    # don't see a stale body from a prior test's request shape.
    clearer = getattr(_cache, "clear_peaks_response_cache", None)
    if callable(clearer):
        clearer()
    # Also drop the per-URL parsed peaks cache so a missing-file test
    # doesn't see a hit from a sibling test that installed peaks.
    peaks_url_cache = getattr(_cache, "_PEAKS_CACHE", None)
    if isinstance(peaks_url_cache, dict):
        peaks_url_cache.clear()
    # Drop the audio_meta sidecar cache too — different tests install
    # different fixtures and the cache is keyed only by slug, so leftover
    # entries pin a stale chapter→URL map across tests.
    try:
        from services.audio import audio_meta as _audio_meta

        _audio_meta._clear_for_test()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def tmp_reciter_dir(tmp_path, monkeypatch):
    """Per-test writable bucket-shape directory rooted under ``tmp_path``.

    Installs a ``FilesystemBackend`` against ``tmp_path`` (v2 backend
    abstraction), so routes/services/loaders read + write under the temp
    dir instead of the real bucket. The on-disk layout matches the bucket
    layout (``reciters/<slug>/segments.json``, etc.); the ``install`` helper
    drops fixtures there and seeds a state row for installed reciters.
    """
    from datetime import datetime, timezone

    from services import hf_bucket as _hf_bucket
    from services import storage_paths as _storage_paths

    monkeypatch.setenv("INSPECTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INSPECTOR_BACKEND", "filesystem")
    monkeypatch.setenv("INSPECTOR_FILESYSTEM_ROOT", str(tmp_path))

    backend = _hf_bucket.FilesystemBackend(tmp_path)
    _hf_bucket.set_backend(backend)

    _invalidate_seg_caches()

    def _install(
        reciter: str,
        fixture_name: str,
        *,
        under_review_for: str | None = None,
    ) -> Path:
        """Install a fixture under ``reciters/<reciter>/`` and seed a state row.

        When ``under_review_for`` is given, the row is seeded as
        ``UNDER_REVIEW`` with that user as the active assignee — required
        for tests that POST to lock-gated routes (save/undo). Default
        ``AWAITING_REVIEW`` keeps existing tests unchanged.
        """
        # Seed a state row so lifecycle-gated routes (lock/save/undo) resolve.
        from services.db import repo_state as _repo_state

        if not _repo_state.exists(reciter):
            if under_review_for is None:
                _seed_state(reciter, state="awaiting_review")
            else:
                _seed_state(
                    reciter,
                    state="under_review",
                    assignee_hf_id=under_review_for,
                    assignee_login="test_user",
                )

        # Install fixture file at reciters/<reciter>/detailed.json
        src = FIXTURES_DIR / f"{fixture_name}.detailed.json"
        detailed_rel = _storage_paths.detailed_path(reciter)
        with open(src, "rb") as f:
            backend.write_bytes_atomic(detailed_rel, f.read())
        dst_path = (tmp_path / detailed_rel).resolve()

        history_src = FIXTURES_DIR / f"{fixture_name}.edit_history.jsonl"
        if history_src.exists():
            with open(history_src, "rb") as f:
                backend.write_bytes_atomic(
                    _storage_paths.edit_history_path(reciter),
                    f.read(),
                )

        # pipeline_meta.json is required for validate (the basmala_amin rule
        # reads ``deleted_basmala_chapters`` from this sidecar — see
        # services/validation/__init__.py::_read_deleted_basmala_chapters).
        # Fixtures don't ship a pipeline_meta; seed an empty one so tests
        # exercise the post-migration code path without hitting hard-fail.
        from qua_shared.schemas import PipelineMeta

        pipeline_meta_doc = PipelineMeta(
            schema_version=1,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            deleted_basmala_chapters=[],
        ).model_dump(mode="json")
        backend.write_json_atomic(
            _storage_paths.pipeline_meta_path(reciter),
            pipeline_meta_doc,
        )

        # Build a matching segments.json so consumers that read both files
        # see a consistent on-disk state for the fixture.
        try:
            from services.save import rebuild_segments_json  # type: ignore
        except ImportError:
            rebuild_segments_json = None  # type: ignore[assignment]
        if rebuild_segments_json is not None:
            doc = json.loads((tmp_path / detailed_rel).read_bytes())
            entries = doc.get("entries", [])
            meta = doc.get("_meta", {})
            seg_rel = _storage_paths.segments_path(reciter)
            if not backend.exists(seg_rel):
                backend.write_json_atomic(seg_rel, {"_meta": meta})
            rebuild_segments_json(reciter, entries)
            # Re-stamp meta if rebuild dropped it.
            seg_doc = backend.read_json(seg_rel)
            assert isinstance(seg_doc, dict)
            if not seg_doc.get("_meta"):
                seg_doc["_meta"] = meta
                backend.write_json_atomic(seg_rel, seg_doc)

        _invalidate_seg_caches()
        return dst_path

    def _seed_under_review(reciter: str, hf_user_id: str) -> None:
        """Seed an UNDER_REVIEW state row for ``reciter`` with ``hf_user_id``
        as the assignee, without copying a fixture. Used by tests that
        hand-author their own ``detailed.json`` and just need the lock
        decorator to pass."""
        from services.db import repo_state as _repo_state

        if _repo_state.exists(reciter):
            return
        _seed_state(
            reciter,
            state="under_review",
            assignee_hf_id=hf_user_id,
            assignee_login="test_user",
        )

    yield type(
        "TmpReciter",
        (),
        {
            "root": tmp_path / "reciters",
            "install": staticmethod(_install),
            "seed_under_review": staticmethod(_seed_under_review),
            "data_dir": tmp_path,
            "backend": backend,
        },
    )

    # Drop the backend singleton at teardown so the next test re-installs fresh.
    _hf_bucket.reset_backend()


def assert_keys_superset(
    baseline_keys: list[str],
    response_keys: list[str],
    route_name: str,
) -> None:
    """Assert that *response_keys* is a superset of *baseline_keys* (MUST-1).

    Any key present in the baseline must remain present in the live response.
    New keys are allowed (additive-only contract); missing keys are failures.
    """
    missing = set(baseline_keys) - set(response_keys)
    assert not missing, (
        f"MUST-1 violation on {route_name!r}: "
        f"baseline keys no longer in response: {sorted(missing)!r}. "
        f"Baseline had {sorted(baseline_keys)!r}; "
        f"live response has {sorted(response_keys)!r}."
    )
