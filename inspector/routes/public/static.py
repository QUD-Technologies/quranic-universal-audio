"""Static-data routes (/api/static/*).

Read-only projections of in-memory services for the frontend. Each route
picks its own cache discipline:

- ``/catalog.json`` — short TTL; catalog edits should propagate quickly.
- ``/quran-refs.json`` — immutable + content-hashed; reference data is
  fixed across users / reciters / sessions and only changes on a rebuild.
- ``/quran-refs/version`` — tiny version probe, no-cache; FE polls once at
  app boot to learn the current hash and cache-bust the payload URL.
- ``/edition/<riwayah>/font.<ext>`` — immutable + digest-ETagged; the paired
  font for a non-Hafs edition, served out of the packaged ``qua_domain`` asset.
- ``/mushaf-font/<name>`` — immutable; the Mushaf view's header + 1405H fonts,
  read from the bucket ``reference/mushaf-fonts/``.

The two ``quran-refs`` routes take an optional ``?riwayah=`` (an **Inspector**
slug, the vocabulary the catalog stores on a delivery). Omitted, they serve
Hafs, so the FE's existing request is unchanged. Neither is capability-gated:
the payload is the published Quranic text, identical for every viewer.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import orjson
from flask import Blueprint, Response, abort, jsonify, request, send_file

from routes._riwayah_param import sdk_riwayah_param
from services import auth as auth_service
from services import catalog as catalog_service
from services import permissions
from services import quran_refs as quran_refs_service
from services.reference import editions as editions_service

static_bp = Blueprint("static_data", __name__, url_prefix="/api/static")


# The catalog projection differs for owners (who may inspect EveryAyah), so it
# must not be shared through a public browser/proxy cache.
_CATALOG_CACHE_CONTROL = "public, max-age=300"

# Quran-refs payload is content-hashed; immutable means the browser never
# revalidates within the cache lifetime, and the FE busts via ``?v=<hash>``
# on the rare deploy that rebuilds Digital Khatt or surah metadata.
_QURAN_REFS_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Edition fonts are ~0.9 MB and keyed by a content digest, so they cache the
# same way. The extension is fixed by the packaged asset, not the request.
_EDITION_FONT_CACHE_CONTROL = "public, max-age=31536000, immutable"


@static_bp.route("/catalog.json")
def catalog_json() -> Response:
    """Serve the in-memory ``ReciterCatalog`` snapshot as JSON.

    Shape matches ``qua_shared.schemas.ReciterCatalog`` — the frontend
    reads ``reciters[]`` + ``deliveries[]`` to build the Timestamps tab
    reciter dropdown.
    """
    user = auth_service.current_user()
    snapshot = catalog_service.for_viewer(
        include_everyayah=user is not None and permissions.is_owner(user)
    )
    body = orjson.dumps(snapshot.model_dump(mode="json", by_alias=True))
    response = Response(body, mimetype="application/json")
    response.headers["Cache-Control"] = (
        "private, no-store" if user is not None else _CATALOG_CACHE_CONTROL
    )
    response.headers["Vary"] = "Cookie"
    return response


@contextmanager
def _edition_available():
    """503 rather than 500 when the edition package is absent from this build.

    A Hafs-only image serves Hafs from its own data and raises for every other
    edition. Letting that reach the catch-all answers a documented, expected
    state with "internal server error" and no way for the client to tell it
    from a real fault.
    """
    try:
        yield
    except editions_service.EditionsUnavailable as exc:
        abort(503, str(exc))


@static_bp.route("/quran-refs/version")
def quran_refs_version() -> Response:
    """Return the current Quran-refs payload hash for cache busting."""
    riwayah = sdk_riwayah_param(request.args.get("riwayah"))
    with _edition_available():
        version = quran_refs_service.payload_hash(riwayah)
    response = jsonify({"version": version})
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@static_bp.route("/quran-refs.json")
def quran_refs_json() -> Response:
    """Serve one edition's word map + verse word counts + verse-marker prefix.

    Bytes are built once per edition and memoised (see
    ``services/reference/quran_refs.py``); each request just hands them back
    with immutable cache headers + an ETag matching the version endpoint.
    """
    riwayah = sdk_riwayah_param(request.args.get("riwayah"))
    with _edition_available():
        body = quran_refs_service.build_payload(riwayah)
        digest = quran_refs_service.payload_hash(riwayah)
    response = Response(body, mimetype="application/json")
    response.headers["Cache-Control"] = _QURAN_REFS_CACHE_CONTROL
    response.headers["ETag"] = f'"{digest}"'
    return response


@static_bp.route("/edition/<riwayah>/font")
def edition_font(riwayah: str) -> Response:
    """Serve a non-Hafs edition's paired font.

    Hafs is deliberately absent: its Digital Khatt font ships with the frontend
    bundle as an inlined data URI (HF Spaces do not smudge LFS at build time),
    and the ``qua_domain`` Hafs font pairs with a different glyph variant.

    503, never a substitute font, when the edition package is missing — a
    fallback font would render the edition's script with the wrong ligatures
    and stop marks.
    """
    slug = sdk_riwayah_param(riwayah)
    try:
        payload, asset = editions_service.font(slug)
    except editions_service.HafsNotRoutedHere:
        abort(404, "Hafs ships its font with the frontend bundle")
    except editions_service.EditionsUnavailable as exc:
        abort(503, str(exc))
    response = Response(payload, mimetype=asset.media_type)
    response.headers["Cache-Control"] = _EDITION_FONT_CACHE_CONTROL
    response.headers["ETag"] = f'"{asset.sha256}"'
    response.headers["Content-Disposition"] = f'inline; filename="{asset.filename}"'
    return response


# Timestamps Mushaf-view fonts (surah-name ligatures, surah-header frame, the
# 1405H Digital Khatt v1 text font). Bucket-only for the same LFS reason as
# the guide clips below: a binary woff2 in the image is a pointer stub on the
# Space, the Xet-backed bucket always carries the real bytes. Whitelisted —
# the name is interpolated into a bucket path.
_MUSHAF_FONT_BUCKET_DIR = "reference/mushaf-fonts"
_MUSHAF_FONTS = frozenset(
    {"surah-name-v2.woff2", "juz-font.woff2", "digital-khatt-madani-v1.woff2"}
)
_MUSHAF_FONT_CACHE_CONTROL = "public, max-age=31536000, immutable"


@static_bp.route("/mushaf-font/<name>")
def mushaf_font(name: str) -> Response:
    """Serve one Mushaf-view webfont from the bucket (404 if unknown/absent)."""
    if name not in _MUSHAF_FONTS:
        abort(404)
    from services.storage.hf_bucket import get_backend

    try:
        body = get_backend().read_bytes(f"{_MUSHAF_FONT_BUCKET_DIR}/{name}")
    except Exception:
        abort(404)
    resp = Response(body, mimetype="font/woff2")
    resp.headers["Cache-Control"] = _MUSHAF_FONT_CACHE_CONTROL
    return resp


# Segments-guide example clips. Served here (not as a plain dist static asset)
# because on the deployed Space HF auto-LFS promotes the larger ``.mp3`` (by
# size, ~100 KB+), so the in-image ``dist/guide-audio/`` copy is a git-lfs
# pointer stub. The bucket (Xet-backed) always carries the real bytes, so we
# resolve local-dist-first then fall back to ``reference/guide-audio/``.
_GUIDE_AUDIO_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist" / "guide-audio"
_GUIDE_AUDIO_BUCKET_DIR = "reference/guide-audio"
_GUIDE_AUDIO_CACHE_CONTROL = "public, max-age=31536000, immutable"
_GUIDE_AUDIO_NAME_RE = re.compile(r"^[a-z0-9_]+\.mp3$")


def _is_lfs_pointer(p: Path) -> bool:
    """True if ``p`` is an unsmudged git-LFS pointer stub, not the real bytes."""
    try:
        with p.open("rb") as f:
            return f.read(40).startswith(b"version https://git-lfs")
    except OSError:
        return False


@static_bp.route("/guide-audio/<name>")
def guide_audio(name: str) -> Response:
    """Serve a segments-guide example mp3 clip (real bytes, never an LFS stub).

    Resolves the local ``dist/guide-audio/<name>`` first (real in dev / a
    correctly-shipped image), else the bucket ``reference/guide-audio/<name>``.
    """
    if not _GUIDE_AUDIO_NAME_RE.match(name):
        abort(404)
    local = _GUIDE_AUDIO_DIST / name
    if local.is_file() and not _is_lfs_pointer(local):
        resp = send_file(str(local), mimetype="audio/mpeg", conditional=True)
    else:
        from services.storage.hf_bucket import get_backend

        try:
            body = get_backend().read_bytes(f"{_GUIDE_AUDIO_BUCKET_DIR}/{name}")
        except Exception:
            abort(404)
        # send_file over BytesIO (not a bare Response) so Werkzeug honours Range:
        # clip playback seeks into the file, so the larger clips need 206s.
        resp = send_file(
            BytesIO(body), mimetype="audio/mpeg", conditional=True, etag=f"guide-{name}-{len(body)}"
        )
    resp.headers["Cache-Control"] = _GUIDE_AUDIO_CACHE_CONTROL
    resp.headers["Accept-Ranges"] = "bytes"
    return resp
