"""``canonicalize_mp3quran_urls`` — legacy ``serverN`` chapter urls → their CDN target."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from canonicalize_mp3quran_urls import (
    ResolveError,
    is_legacy,
    resolve_redirect,
    rewrite_manifest,
)

LEGACY = "https://server6.mp3quran.net/kurdi/024.mp3"
CDN = "https://cdn.mp3quran.net/audio/raad-kurdi/r1/024.mp3"


@dataclass
class _Resp:
    status_code: int
    headers: dict = field(default_factory=dict)


def _head(routes: dict[str, _Resp]):
    calls: list[str] = []

    def head(url, **_kwargs):
        calls.append(url)
        return routes[url]

    head.calls = calls
    return head


def _manifest(chapters: dict) -> dict:
    return {
        "schema_version": 1,
        "slug": "raad_al_kurdi_mp3quran",
        "_meta": {"checksum": "sha256:old", "chapter_count": len(chapters)},
        "chapters": chapters,
    }


@pytest.mark.parametrize(
    ("url", "legacy"),
    [
        (LEGACY, True),
        ("http://server11.mp3quran.net/a/001.mp3", True),
        (CDN, False),
        ("https://download.quranicaudio.com/quran/x/001.mp3", False),
        ("reciters/x/audio/1.mp3", False),
    ],
)
def test_is_legacy_matches_only_numbered_mp3quran_servers(url, legacy):
    assert is_legacy(url) is legacy


def test_resolve_redirect_returns_the_live_cdn_target():
    head = _head({LEGACY: _Resp(301, {"location": CDN}), CDN: _Resp(200)})

    assert resolve_redirect(LEGACY, head=head) == CDN
    assert head.calls == [LEGACY, CDN]


@pytest.mark.parametrize(
    ("routes", "reason"),
    [
        ({LEGACY: _Resp(200)}, "no redirect"),
        (
            {LEGACY: _Resp(301, {"location": "https://evil.example/024.mp3"})},
            "unexpected host",
        ),
        (
            {LEGACY: _Resp(301, {"location": CDN.replace("024", "025")})},
            "file name changed",
        ),
        ({LEGACY: _Resp(301, {"location": CDN}), CDN: _Resp(404)}, "target answered 404"),
    ],
)
def test_resolve_redirect_refuses_anything_but_a_live_same_file_cdn_target(routes, reason):
    with pytest.raises(ResolveError, match=reason):
        resolve_redirect(LEGACY, head=_head(routes))


def test_rewrite_manifest_swaps_legacy_urls_and_restamps_the_checksum():
    doc = _manifest(
        {
            "24": {"url": LEGACY, "size_bytes": 1, "duration_sec": 2},
            "25": {"url": "https://download.quranicaudio.com/q/025.mp3"},
        }
    )

    new, rewrites = rewrite_manifest(doc, {LEGACY: CDN}.__getitem__)

    assert rewrites == [("24", LEGACY, CDN)]
    assert new["chapters"]["24"] == {"url": CDN, "size_bytes": 1, "duration_sec": 2}
    assert new["chapters"]["25"] == doc["chapters"]["25"]
    assert new["_meta"]["checksum"] not in ("sha256:old", "")
    assert doc["chapters"]["24"]["url"] == LEGACY  # input untouched


def test_rewrite_manifest_without_legacy_urls_changes_nothing():
    doc = _manifest({"1": {"url": CDN}})

    new, rewrites = rewrite_manifest(doc, lambda url: pytest.fail(f"resolved {url}"))

    assert rewrites == []
    assert new == doc
