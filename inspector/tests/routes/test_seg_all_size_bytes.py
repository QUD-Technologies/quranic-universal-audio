"""``/api/seg/all`` ships the manifest size per chapter URL.

The FE plays a CDN URL directly only when the live file still has the size
recorded in the audio-manifest sidecar, so the route must pass each sized
chapter through and leave unsized chapters out (they stay on the proxy).
"""

from __future__ import annotations

from services.audio import audio_meta

SIZED_URL = "https://cdn.example/112.mp3"
UNSIZED_URL = "https://cdn.example/113.mp3"


def test_seg_all_maps_manifest_size_by_url(flask_client, tmp_reciter_dir):
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    audio_meta._stage_for_test(
        reciter,
        {
            "chapters": {
                "112": {"url": SIZED_URL, "duration_sec": 20, "size_bytes": 320_417},
                "113": {"url": UNSIZED_URL, "duration_sec": 25},
            }
        },
    )

    res = flask_client.get(f"/api/seg/all/{reciter}")

    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["size_bytes_by_url"] == {SIZED_URL: 320_417}
