"""Playback metadata must preserve the audio manifest's recording identity."""

from __future__ import annotations

import types


def test_quranicaudio_route_keeps_manifest_url_and_duration(flask_client, monkeypatch):
    """A reciter/style match must never substitute a different performance.

    Timings, bitrate metadata, peaks, and bucket audio are all keyed to the
    manifest URL.  Replacing that URL at read time breaks their shared timebase.
    """
    from routes.audio import metadata

    from services.storage import cache

    manifest_url = "https://download.quranicaudio.com/quran/mishaari_california/001.mp3"
    monkeypatch.setattr(metadata.state_service, "has_audio_access", lambda slug: True)
    cache._audio_url.clear()
    monkeypatch.setattr(
        metadata,
        "get_backend",
        lambda: types.SimpleNamespace(
            read_json=lambda path: {
                "chapters": {
                    "1": {
                        "url": manifest_url,
                        "duration_sec": 47.054567,
                    }
                }
            }
        ),
    )
    response = flask_client.get(
        "/api/audio/surahs/by_surah/quranicaudio/mishary_rashid_al_afasy_2008_qdc"
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "surahs": {
            "1": {
                "url": manifest_url,
                "duration_ms": 47055,
            }
        }
    }
