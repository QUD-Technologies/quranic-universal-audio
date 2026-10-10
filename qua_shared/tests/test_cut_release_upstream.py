"""``cut_release._upstream_changes`` — the cut refuses replaced upstream recordings."""

from __future__ import annotations

import json

from qua_jobs import cut_release
from qua_shared.audio import upstream


def _manifest(root, slug, chapters):
    path = root / "catalog" / "audio_manifest" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"chapters": chapters}), encoding="utf-8")


def test_lists_only_recitations_with_a_replaced_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPECTOR_BUCKET_MOUNT", str(tmp_path))
    entry = {"size_bytes": 1000, "duration_sec": 465}
    _manifest(tmp_path, "swapped", {"62": {**entry, "url": "https://cdn/swapped/62.mp3"}})
    _manifest(tmp_path, "intact", {"1": {**entry, "url": "https://cdn/intact/1.mp3"}})
    probes = {
        "https://cdn/swapped/62.mp3": {"size": 800, "duration_s": 321.0},
        "https://cdn/intact/1.mp3": {"size": 1000, "duration_s": 465.0},
    }
    monkeypatch.setattr(upstream, "probe", probes.__getitem__)

    changed = cut_release._upstream_changes(
        [{"slug": "swapped"}, {"slug": "intact"}, {"slug": "no_manifest"}]
    )

    assert changed == {"swapped": ["ch62 recording (465s → 321s)"]}
