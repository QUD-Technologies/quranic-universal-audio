"""The Timestamps resource comes from the talqeen-quran-data pack and degrades, never raises.

``/api/ts/resource/digital_khatt`` is built from ``qua_shared.quran_text``. A missing SDK
or pack must skip it (the manifest still builds). The QPC-shaped ``qpc_hafs`` and the
per-layout ``digital_khatt_<layout>`` resources are retired: the frontend reads one Digital
Khatt text, and the Mushaf view each layout's stops from the pack itself.
"""

from __future__ import annotations

import gzip
import json

from qua_shared import quran_text
from services.reference import timestamps as ts_manifest


def _raise_unavailable() -> bytes:
    raise quran_text.QuranDataUnavailable("no pack")


def test_build_resource_bytes_degrades_without_pack(monkeypatch):
    """An unavailable pack is skipped, not raised — the manifest must build."""
    monkeypatch.setattr(ts_manifest.quran_text, "digital_khatt_script_bytes", _raise_unavailable)

    assert ts_manifest._build_resource_bytes() == {}


def test_build_resource_bytes_gzips_the_digital_khatt_script(monkeypatch):
    monkeypatch.setattr(ts_manifest.quran_text, "digital_khatt_script_bytes", lambda: b'{"d": 1}')

    out = ts_manifest._build_resource_bytes()

    assert list(out) == ["digital_khatt"]
    assert gzip.decompress(out["digital_khatt"]) == b'{"d": 1}'


def test_retired_resources_are_404(flask_client):
    for name in ("qpc_hafs", "digital_khatt_madani-1405"):
        assert flask_client.get(f"/api/ts/resource/{name}").status_code == 404
    res = flask_client.get("/api/ts/resource/digital_khatt")
    assert res.status_code == 200
    assert json.loads(gzip.decompress(res.data)) == quran_text.digital_khatt_script()
