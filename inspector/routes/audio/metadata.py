"""Audio tab metadata routes (/api/audio/*)."""

from flask import Blueprint, jsonify

from qua_shared.schemas import AudioSurahsResponse, ErrorEnvelope
from services import audio_fetch, cache, storage_paths
from services import state as state_service
from services.hf_bucket import StorageNotFound, get_backend

audio_meta_bp = Blueprint("audio_meta", __name__, url_prefix="/api/audio")


@audio_meta_bp.route("/surahs/<category>/<source>/<slug>")
def audio_surahs(category, source, slug):
    """Return per-chapter ``{url, duration_ms}`` for a delivery.

    Reads the per-delivery audio_manifest sidecar from
    ``<bucket>/catalog/audio_manifest/<slug>.json``. ``duration_ms`` is
    derived from the sidecar's ``duration_sec`` so the dashboard player can
    show full chapter length before the browser fetches MP3 headers — the
    ``BottomPlayer`` runs ``<audio preload="none">`` and would otherwise
    show ``0:00`` until first play.

    When the sidecar has a null/missing duration for a chapter (e.g. probed
    before the manifest carried durations), falls back to the duration baked
    into the slim peaks header (``reciters/<slug>/peaks/<ch>.json.gz``) via
    ``audio_fetch.read_prefetched_peaks_duration_ms``. Stays ``None`` only
    when peaks are also absent.
    """
    if not state_service.has_audio_access(slug):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    key = f"{category}/{source}/{slug}"
    cached = cache.get_audio_url_cache(key)
    if cached is not None:
        return jsonify(_serialize_surahs(cached))
    try:
        doc = get_backend().read_json(storage_paths.audio_manifest_path(slug))
    except StorageNotFound:
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    if not isinstance(doc, dict):
        return (
            jsonify(
                ErrorEnvelope(error="invalid audio_manifest sidecar").model_dump(exclude_none=True)
            ),
            500,
        )
    chapters = doc.get("chapters") or {}
    surahs: dict[str, dict] = {}
    for k, v in chapters.items():
        if isinstance(v, dict):
            url = v.get("url")
            if not isinstance(url, str):
                continue
            duration_sec = v.get("duration_sec")
            duration_ms = (
                int(round(duration_sec * 1000)) if isinstance(duration_sec, (int, float)) else None
            )
        elif isinstance(v, str):
            url, duration_ms = v, None
        else:
            continue
        if duration_ms is None:
            # Manifest never carried a length for this chapter — fall back to
            # the duration baked into the slim peaks header so the dashboard
            # scrubber shows a real length instead of 0:00.
            duration_ms = audio_fetch.read_prefetched_peaks_duration_ms(slug, url)
        surahs[k] = {"url": url, "duration_ms": duration_ms}
    cache.set_audio_url_cache(key, surahs)
    return jsonify(_serialize_surahs(surahs))


def _serialize_surahs(surahs: dict[str, dict]) -> dict:
    """Serialize the per-chapter ``surahs`` map through the wire model.

    Dumps with ``by_alias`` and no ``exclude_none`` so the required-nullable
    ``duration_ms`` key remains present when no length is known.
    """
    return AudioSurahsResponse.model_validate({"surahs": surahs}).model_dump(by_alias=True)
