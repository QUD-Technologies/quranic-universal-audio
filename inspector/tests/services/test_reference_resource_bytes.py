"""The Timestamps resources come from the talqeen-quran-data pack and degrade, never raise.

``/api/ts/resource/{qpc_hafs,digital_khatt,digital_khatt_<layout>}`` are built from
``qua_shared.quran_text``.
A missing SDK or pack must skip them (the manifest still builds), and both resources
carry the same Digital Khatt word text — QPC Hafs text is retired.
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
    monkeypatch.setattr(ts_manifest.quran_text, "hafs_word_script_bytes", _raise_unavailable)

    assert ts_manifest._build_resource_bytes() == {}


def test_build_resource_bytes_gzips_both(monkeypatch):
    monkeypatch.setattr(ts_manifest.quran_text, "hafs_word_script_bytes", lambda: b'{"q": 1}')
    monkeypatch.setattr(ts_manifest.quran_text, "digital_khatt_script_bytes", lambda: b'{"d": 1}')

    out = ts_manifest._build_resource_bytes()

    assert gzip.decompress(out["qpc_hafs"]) == b'{"q": 1}'
    assert gzip.decompress(out["digital_khatt"]) == b'{"d": 1}'


def test_qpc_hafs_resource_carries_digital_khatt_text():
    out = ts_manifest._build_resource_bytes()
    words = json.loads(gzip.decompress(out["qpc_hafs"]))
    script = json.loads(gzip.decompress(out["digital_khatt"]))

    assert len(words) == 77_433
    assert all(script[ref]["text"] == entry["text"] for ref, entry in words.items())


def test_manifest_advertises_one_resource_per_stop_edition_layout():
    assert ts_manifest._resource_keys() == [
        "qpc_hafs",
        "digital_khatt",
        "digital_khatt_madani-1405",
    ]


def test_layout_resource_is_the_dk_script_with_that_layouts_stops():
    body = ts_manifest._build_layout_resource("digital_khatt_madani-1405")
    script = json.loads(gzip.decompress(body))

    assert script == quran_text.digital_khatt_script("madani-1405")
    assert script != quran_text.digital_khatt_script()


def test_layout_resource_unknown_names_are_none(monkeypatch):
    assert ts_manifest._build_layout_resource("digital_khatt_madani-1421") is None
    assert ts_manifest._build_layout_resource("qpc_hafs") is None
    monkeypatch.setattr(ts_manifest.quran_text, "layout_stops", _raise_unavailable)
    assert ts_manifest._resource_keys() == ["qpc_hafs", "digital_khatt"]
    assert ts_manifest._build_layout_resource("digital_khatt_madani-1405") is None


def test_resource_route_serves_the_layout_resource_lazily(flask_client):
    res = flask_client.get("/api/ts/resource/digital_khatt_madani-1405")

    assert res.status_code == 200
    assert json.loads(gzip.decompress(res.data)) == quran_text.digital_khatt_script("madani-1405")
    assert flask_client.get("/api/ts/resource/digital_khatt_madani-1441").status_code == 404
