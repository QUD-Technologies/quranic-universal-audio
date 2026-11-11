"""Audio proxy route (``/api/seg/audio-proxy``).

Single GET route. Lookup order:

1. **Bucket-resident audio** — ``reciters/<slug>/audio/<chapter>.mp3`` written
   by the align pipeline's acquire / split HF jobs.
   Served via ``range_file.send_range_file`` (1 MB reads, Range + 304).
2. **CDN stream-through** — for slugs the extraction pipeline hasn't
   reached yet (anonymous browsing, pre-extraction onboarding, by_ayah
   deliveries). We stream the upstream response through Flask instead of
   302-redirecting so the bytes always arrive same-origin from the
   browser's perspective with explicit CORS headers. The naive 302 fails
   for ``<audio crossorigin="anonymous">``: the redirect response itself
   carries no ``Access-Control-Allow-Origin`` header, so even though
   every published-reciter CDN sends ACAO on the final response, the
   redirect chain is treated as opaque by Chrome's Fetch impl and
   ``MediaElementAudioSourceNode`` produces silence.

The download-all + delete-cache + cache-status endpoints have been removed
— bucket audio is populated by the offline pipeline, not by a Space-side
warm path, and is never GC'd.

Optional query params:

* ``download=1`` — attach a ``Content-Disposition: attachment`` header so the
  browser saves the chapter instead of playing it inline (drives the
  dashboard footer's download button). Honoured on every branch (bucket,
  in-memory, CDN stream-through).
* ``chapter=<n>`` — surah number, used only to build the download filename
  ``<reciter>-<NNN>.mp3``.
"""

import logging
import re
from io import BytesIO

import requests
from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

from config import AUDIO_CACHE_MAX_AGE, AUDIO_MIME_TYPES
from services import audio_source
from services import state as state_service

from .range_file import send_range_file

logger = logging.getLogger(__name__)

audio_proxy_bp = Blueprint("audio_proxy", __name__, url_prefix="/api/seg")

# Stream the CDN response through Flask in 64 KB chunks. Matches the
# Range-fetch granularity browsers use for `<audio>` scrubbing.
_STREAM_CHUNK_BYTES = 64 * 1024
# Cap a single upstream fetch at 30 s — covers cold CDN edge nodes without
# letting a hung connection block the single Flask worker indefinitely.
_UPSTREAM_TIMEOUT_SECS = 30


def _download_name(reciter: str, chapter: str) -> str:
    """Build a safe download filename ``<reciter>-<NNN>.mp3``.

    Strips the slug to ``[A-Za-z0-9_-]`` (the header value is otherwise
    attacker-influenced) and zero-pads a numeric chapter to 3 digits. Falls
    back to ``<reciter>.mp3`` when no usable chapter is supplied.
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "", reciter) or "surah"
    chapter = chapter.strip()
    if chapter.isdigit():
        return f"{safe}-{int(chapter):03d}.mp3"
    return f"{safe}.mp3"


def _stream_cdn(url: str, disposition: str | None = None) -> Response:
    """Pull *url* from the CDN and stream it back to the caller as a
    same-origin response with explicit CORS headers. Forwards the browser's
    ``Range`` header so partial-content requests stay byte-range accurate.

    Why same-origin matters: ``<audio crossorigin="anonymous">`` paired with
    the Web Audio kill-switch (``MediaElementAudioSourceNode``) silences
    the audio if any link in the request chain has missing/invalid CORS
    headers. Flask's ``redirect(url, 302)`` adds no ACAO, so the chain
    breaks even when the final CDN response sends ``ACAO: *``. Streaming
    keeps the response same-origin and we attach ACAO ourselves.

    This branch dies the day every reciter has bucket-cached audio (see
    module docstring).
    """
    req_headers = {"User-Agent": "Mozilla/5.0"}
    if rng := request.headers.get("Range"):
        req_headers["Range"] = rng
    try:
        upstream = requests.get(
            url,
            headers=req_headers,
            stream=True,
            timeout=_UPSTREAM_TIMEOUT_SECS,
        )
    except requests.RequestException as exc:
        logger.warning("audio_proxy: upstream fetch failed url=%s err=%s", url, exc)
        resp = jsonify({"error": "upstream audio fetch failed"})
        resp.status_code = 502
        return resp

    # Cache only successful bodies. A forwarded upstream error (4xx/5xx) must NOT
    # carry the immutable 1-year Cache-Control, or the browser pins the failure
    # and replays it until a hard cache clear.
    ok = upstream.status_code in (200, 206)
    out_headers = {
        "Content-Type": upstream.headers.get("Content-Type", "audio/mpeg"),
        "Accept-Ranges": "bytes",
        "Cache-Control": f"public, max-age={AUDIO_CACHE_MAX_AGE}, immutable" if ok else "no-store",
        "Access-Control-Allow-Origin": "*",
    }
    if disposition:
        out_headers["Content-Disposition"] = disposition
    # Forward Content-Length / Content-Range so the browser knows the
    # response size and which byte slice it just got (Werkzeug fills these
    # automatically only on local-file send_file paths).
    for h in ("Content-Length", "Content-Range"):
        if h in upstream.headers:
            out_headers[h] = upstream.headers[h]

    def _stream():
        try:
            for chunk in upstream.iter_content(_STREAM_CHUNK_BYTES):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(_stream()),
        status=upstream.status_code,
        headers=out_headers,
    )


@audio_proxy_bp.route("/audio-proxy/<reciter>")
def seg_audio_proxy(reciter):
    """Proxy/serve a chapter MP3 via the shared audio-source resolver.

    Priority: mount/disk path (streamed in 1 MB reads with Range + ETag/304)
    → in-memory bytes (local-dev fallback, still served with Range via a
    seekable BytesIO) → CDN stream-through (legacy reciters without
    bucket-cached audio). Browser issues partial-content requests once the
    first response advertises ``Accept-Ranges``; subsequent seeks are
    byte-range fetches handled by ``send_range_file`` (local) or
    re-streamed through ``_stream_cdn`` with a fresh ``Range`` header.
    """
    if not state_service.has_audio_access(reciter):
        return jsonify({"error": "Reciter not found"}), 404
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No url provided"}), 400

    # When download=1, force a save (Content-Disposition: attachment) so the
    # browser downloads the chapter instead of playing it inline — drives the
    # dashboard footer's download button. `chapter` only shapes the filename.
    download = request.args.get("download", "").strip().lower() in ("1", "true", "yes")
    disposition = (
        f'attachment; filename="{_download_name(reciter, request.args.get("chapter", ""))}"'
        if download
        else None
    )

    src = audio_source.resolve(reciter, url)
    immutable = f"public, max-age={AUDIO_CACHE_MAX_AGE}, immutable"

    if src.path is not None:
        # NOT send_file: its Range path reads the mount in 8 KB pieces, which
        # over the Space's NFS bucket mount throttles to ~100-200 KB/s per
        # stream (see range_file.py). Stream 1 MB reads instead.
        mime = AUDIO_MIME_TYPES.get(src.path.suffix.lower(), "audio/mpeg")
        resp = send_range_file(request, src.path, mime)
        resp.headers["Cache-Control"] = immutable
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        if disposition:
            resp.headers["Content-Disposition"] = disposition
        return resp

    if src.data is not None:
        resp = send_file(
            BytesIO(src.data),
            mimetype="audio/mpeg",
            conditional=True,
            etag=f"{src.chapter_key or 'chapter'}-{len(src.data)}",
        )
        resp.headers["Cache-Control"] = immutable
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        if disposition:
            resp.headers["Content-Disposition"] = disposition
        return resp

    return _stream_cdn(url, disposition)
