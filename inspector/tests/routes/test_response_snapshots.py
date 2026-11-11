"""Pre-integration response-snapshot baseline — the Phase-5 regression net.

For every MODELED GET endpoint, this hits the route through the Flask test
client with the committed fixtures (the SAME fixture/test-client/DB-seeding
setup the match-route tests in ``test_wire_{seg,ts,public,audio}_models.py``
use), normalizes the handful of volatile fields with a pinned normalizer, and
snapshots the response body to a committed JSON file under
``inspector/tests/routes/snapshots/<endpoint>.json``.

Capture vs regression
---------------------
Each test calls :func:`_snapshot`. The FIRST run (file absent) WRITES the
baseline and passes — that capture step records the current hand-built output.
Every subsequent run (file present) ASSERTS the normalized body equals the
committed snapshot byte-for-byte, so a Phase-5 route refactor that drifts the
wire fails loudly here.

Volatile fields
---------------
The only non-deterministic field across these endpoints is the manifest's
``generated_at`` ISO timestamp; ``_normalize`` pins it (and any future
same-named field) to a sentinel. Everything else is deterministic given the
committed fixtures + the fixed-date state seeds, so it is snapshotted verbatim
(segment uids come from the 112-ikhlas fixture, ``state_since`` /
``last_activity`` from the seeded fixed dates).
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qua_shared.schemas import AudioCategory, Delivery, ReciterEntry

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

# Keys whose values vary run-to-run (wall-clock / build provenance). Replaced
# with a stable sentinel before snapshotting so the baseline stays byte-stable.
_VOLATILE_KEYS = {"generated_at"}
_SENTINEL = "<normalized>"


def _normalize(value):
    """Recursively replace volatile field values with a pinned sentinel.

    Walks dicts/lists; any key in ``_VOLATILE_KEYS`` has its value swapped for
    ``_SENTINEL`` regardless of depth. Leaves every other value untouched so
    the snapshot still proves the exact shape + content of the wire.
    """
    if isinstance(value, dict):
        return {k: (_SENTINEL if k in _VOLATILE_KEYS else _normalize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _snapshot(name: str, body) -> None:
    """Capture (write if absent) or assert (compare if present) a baseline.

    ``body`` is the decoded response payload. It is normalized, then either
    written to ``snapshots/<name>.json`` (first run) or compared against the
    committed file (every run after). The on-disk form is pretty-printed,
    UTF-8, trailing newline — a readable, reviewable diff in PRs.
    """
    normalized = _normalize(body)
    path = SNAPSHOT_DIR / f"{name}.json"
    serialized = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not path.exists():
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
        return
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert normalized == committed, (
        f"response-snapshot drift for {name!r}: the route's wire body no longer "
        f"matches snapshots/{name}.json. If the change is intentional, delete the "
        f"snapshot file and re-run to recapture."
    )


# ---------------------------------------------------------------------------
# fixtures / seeding helpers (mirror the match-route tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def installed(flask_client, tmp_reciter_dir):
    """Install the 112-ikhlas fixture and yield (client, reciter)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    return flask_client, reciter


def _seed_released_ts_reciter(slug: str = "rec_ts") -> None:
    """Seed a complete by_surah catalog reciter+delivery flipped to ``released``
    (mirrors ``test_wire_ts_models._seed_released_reciter_with_catalog``)."""
    from tests.conftest import _seed_catalog, _seed_state

    _seed_catalog(
        reciters=[ReciterEntry(reciter_id="rec_ts", name_en="Reciter TS")],
        deliveries=[
            Delivery(
                slug=slug,
                reciter_id="rec_ts",
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
    _seed_state(slug, state="released")


def _seed_public_husary() -> None:
    """Seed one released ``husary``/``husary_qdc`` reciter with fixed-date state
    (mirrors the public match-route ``_install`` helper, fixed seeds only)."""
    from qua_shared.schemas import AudioCategory, Channel, Riwayah, Source, Style, Vocab
    from services import db
    from services.db import repo_catalog
    from tests.conftest import _seed_state

    vocab = Vocab(
        riwayat=[Riwayah(slug="hafs_an_asim", short="hafs", name="Hafs")],
        styles=[Style(slug="murattal", short="murattal", name="Murattal")],
        sources=[
            Source(
                slug="mp3quran",
                name="mp3quran",
                url="https://mp3quran.net",
                audio_categories=[AudioCategory.BY_SURAH],
            )
        ],
        channels=[
            Channel(slug="mp3quran", short="mp3q", name="MP3 Quran", host_patterns=["mp3quran.net"])
        ],
        recording_contexts=[],
    )
    with db.transaction():
        repo_catalog.load_vocab(vocab)
        repo_catalog.insert_reciter(
            ReciterEntry(reciter_id="husary", name_en="Husary", name_ar="الحصري")
        )
        repo_catalog.insert_delivery(
            Delivery(
                slug="husary_qdc",
                reciter_id="husary",
                riwayah="hafs_an_asim",
                style="murattal",
                source="mp3quran",
                channel="mp3quran",
                audio_category=AudioCategory.BY_SURAH,
                chapter_count=114,
                added_at=datetime(2026, 1, 1, tzinfo=UTC),
                added_by_hf_id="system_seed",
            )
        )
    _seed_state("husary_qdc", state="released", state_since=datetime(2026, 5, 12, tzinfo=UTC))


# ---------------------------------------------------------------------------
# /api/seg/*
# ---------------------------------------------------------------------------


def test_seg_config_snapshot(flask_client):
    res = flask_client.get("/api/seg/config")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_config", res.get_json())


def test_seg_reciters_snapshot(installed):
    client, _ = installed
    res = client.get("/api/seg/reciters")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_reciters", res.get_json())


def test_seg_data_snapshot(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/data/{reciter}/112")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_data", res.get_json())


def test_seg_all_snapshot(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/all/{reciter}")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_all", res.get_json())


def test_seg_validate_snapshot(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_validate", res.get_json())


def test_seg_peaks_snapshot(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/peaks/{reciter}?chapters=112")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("seg_peaks", res.get_json())


# ---------------------------------------------------------------------------
# /api/ts/*
# ---------------------------------------------------------------------------


def test_ts_config_snapshot(flask_client):
    res = flask_client.get("/api/ts/config")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("ts_config", res.get_json())


def test_ts_manifest_snapshot(flask_client, tmp_reciter_dir):
    """The manifest body is gzipped octet-stream — decompress before snapshotting.
    ``generated_at`` is pinned by the normalizer."""
    from services.reference import timestamps as ts_serve

    _seed_released_ts_reciter("rec_ts")
    ts_serve.invalidate()  # drop any manifest cached at an earlier db_seq

    res = flask_client.get("/api/ts/manifest")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = json.loads(gzip.decompress(res.get_data()).decode("utf-8"))
    _snapshot("ts_manifest", body)


def test_ts_vbr_snapshot(flask_client, monkeypatch):
    """Stub ``vbr_chapters_for_reciter`` so the body is deterministic (the
    fixture reciter has no baked VBR chapters)."""
    from routes.timestamps import timestamps as ts_routes

    from tests.conftest import _seed_state

    _seed_state("rec_a", state="released")

    monkeypatch.setattr(
        ts_routes,
        "vbr_chapters_for_reciter",
        lambda reciter: [2, 7] if reciter == "rec_a" else [],
    )
    res = flask_client.get("/api/ts/vbr/rec_a")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("ts_vbr", res.get_json())


# ---------------------------------------------------------------------------
# /api/public/*
# ---------------------------------------------------------------------------


def test_public_stats_snapshot(flask_client):
    _seed_public_husary()
    res = flask_client.get("/api/public/stats")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("public_stats", res.get_json())


def test_public_reciters_snapshot(flask_client):
    _seed_public_husary()
    res = flask_client.get("/api/public/reciters?limit=10")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("public_reciters", res.get_json())


def test_public_reciter_detail_snapshot(flask_client):
    _seed_public_husary()
    res = flask_client.get("/api/public/reciter/husary")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("public_reciter_detail", res.get_json())


# ---------------------------------------------------------------------------
# /api/audio/surahs
# ---------------------------------------------------------------------------


def test_audio_surahs_snapshot(flask_client, tmp_reciter_dir):
    """Install a 2-chapter audio_manifest sidecar (one with duration, one
    without) and snapshot the derived ``{url, duration_ms, size_bytes}`` map."""
    from services import cache, storage_paths
    from tests.conftest import _seed_state

    cache._audio_url.clear()
    slug = "wire_audio_fixture"
    _seed_state(slug, state="awaiting_review")
    tmp_reciter_dir.backend.write_bytes_atomic(
        storage_paths.audio_manifest_path(slug),
        json.dumps(
            {
                "chapters": {
                    "1": {
                        "url": "https://cdn.example/1.mp3",
                        "duration_sec": 12.5,
                        "size_bytes": 200_000,
                    },
                    "2": {"url": "https://cdn.example/2.mp3"},
                }
            }
        ).encode("utf-8"),
    )
    res = flask_client.get(f"/api/audio/surahs/by_surah/quranicaudio/{slug}")
    assert res.status_code == 200, res.get_data(as_text=True)
    _snapshot("audio_surahs", res.get_json())
