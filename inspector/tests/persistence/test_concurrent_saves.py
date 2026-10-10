"""Overlapping saves of different chapters of one reciter all persist."""

from __future__ import annotations

import json
import threading

from qua_shared.schemas import Actor, Role
from services.segments import save
from services.storage import cache, storage_paths
from services.storage.hf_bucket import get_backend

RECITER = "fixture_reciter"
CHAPTERS = ("112", "113", "1")
JOIN_TIMEOUT_S = 10
BLOCKED_PROBE_S = 0.5


def _install_three_chapters(tmp_reciter_dir, load_fixture) -> None:
    tmp_reciter_dir.install(RECITER, "112-ikhlas", under_review_for="test-user-1")
    doc = load_fixture("112-ikhlas")
    doc["entries"] = [
        *doc["entries"],
        *load_fixture("113-falaq")["entries"],
        *load_fixture("synthetic-structural")["entries"],
    ]
    get_backend().write_json_atomic(storage_paths.detailed_path(RECITER), doc)
    cache.invalidate_seg_caches(RECITER)


def _ignore_first_segment(chapter: str) -> dict:
    doc = json.loads(get_backend().read_bytes(storage_paths.detailed_path(RECITER)))
    entry = next(e for e in doc["entries"] if e["ref"] == chapter)
    first = entry["segments"][0]
    return {
        "segments": [
            {
                "index": 0,
                "matched_ref": first["matched_ref"],
                "confidence": first["confidence"],
                "ignored_categories": ["low_confidence"],
            }
        ],
        "operations": [],
    }


def test_overlapping_saves_of_different_chapters_all_persist(
    tmp_reciter_dir, load_fixture, monkeypatch
):
    """Save ``112`` stalls between its load and its write while ``113`` and ``1``
    save. Without one writer at a time, ``1`` loads the document ``113`` wrote
    and ``112`` then writes its older copy over it, dropping ``1``'s edit."""
    _install_three_chapters(tmp_reciter_dir, load_fixture)
    payloads = {ch: _ignore_first_segment(ch) for ch in CHAPTERS}
    actor = Actor(hf_user_id="test-user-1", login_at_time="alice", role=Role.CONTRIBUTOR)

    stalled_loaded = threading.Event()
    release_stalled = threading.Event()
    real_lookups = save._build_seg_lookups

    def lookups(matching):
        if threading.current_thread().name == "save-112":
            stalled_loaded.set()
            release_stalled.wait(JOIN_TIMEOUT_S)
        return real_lookups(matching)

    monkeypatch.setattr(save, "_build_seg_lookups", lookups)
    results: dict[str, object] = {}

    def run(chapter: str) -> None:
        results[chapter] = save.save_seg_data(RECITER, int(chapter), payloads[chapter], actor=actor)

    def start(chapter: str) -> threading.Thread:
        t = threading.Thread(target=run, args=(chapter,), name=f"save-{chapter}")
        t.start()
        return t

    stalled = start("112")
    assert stalled_loaded.wait(JOIN_TIMEOUT_S)
    others = []
    for ch in ("113", "1"):
        others.append(start(ch))
        others[-1].join(BLOCKED_PROBE_S)
    release_stalled.set()
    for t in (stalled, *others):
        t.join(JOIN_TIMEOUT_S)
        assert not t.is_alive()

    assert all(r == {"ok": True} for r in results.values()), results
    cache.invalidate_seg_caches(RECITER)
    doc = json.loads(get_backend().read_bytes(storage_paths.detailed_path(RECITER)))
    by_ref = {e["ref"]: e for e in doc["entries"]}
    for ch in CHAPTERS:
        assert by_ref[ch]["segments"][0].get("ignored_categories") == ["low_confidence"], ch
    assert doc["_meta"], "a save wrote an empty _meta"
