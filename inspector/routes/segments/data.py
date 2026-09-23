"""Segments tab data routes (/api/seg/ — read-only data endpoints)."""

from flask import Blueprint, abort, jsonify, request

from config import (
    ACCORDION_CONTEXT,
    LOW_CONF_DEFAULT_THRESHOLD,
    LOW_CONFIDENCE_THRESHOLDS,
    SEG_FONT_SIZE,
    SEG_SCROLL_ANIM_MODE,
    SEG_WORD_SPACING,
    TRIM_DIM_ALPHA,
    TRIM_PAD_LEFT,
    TRIM_PAD_RIGHT,
)
from constants import (
    QALQALA_LETTERS as _QALQALA_LETTERS,
)
from qua_shared.riwayat import (
    DEFAULT_RIWAYAH,
    UnsupportedRiwayah,
    from_sdk_slug,
    to_sdk_slug,
)
from qua_shared.schemas.wire._envelopes import ErrorEnvelope
from qua_shared.schemas.wire.seg import (
    SegConfigResponse,
    SegDataResponse,
    SegRecitersResponse,
)
from services import cache
from services import state as state_service
from services.audio_meta import chapter_meta, vbr_chapters_for_reciter
from services.auth.auth import current_user
from services.auth.capabilities import can
from services.data_loader import (
    load_detailed,
    resolve_pad,
)
from services.reference import edition_tables
from services.reference.delivery_edition import RiwayahMismatch, sdk_riwayah_for
from services.reference.editions import EditionsUnavailable
from services.segments.flags import flag_view
from services.segments_query import get_chapter_data
from services.state import catalog as catalog_service
from services.validation.registry import ALL_CATEGORIES
from utils.formatting import slug_to_name
from utils.json_response import orjson_cached_response, orjson_response
from utils.references import chapter_from_ref
from utils.uuid7 import uuid7

_DUMP = {"mode": "json", "exclude_none": True, "by_alias": True}

seg_data_bp = Blueprint("seg_data", __name__, url_prefix="/api/seg")


@seg_data_bp.route("/config")
def seg_config():
    """Return display configuration + coordinate vocabularies for Segments.

    ``?riwayah=`` selects the edition (an Inspector slug; default Hafs, whose
    response is byte-identical to the pre-multi-riwayah one apart from the two
    new fields). 400 on an unsupported riwayah rather than a Hafs fallback: the
    FE uses these tables to decide whether a one-word segment is legitimate,
    and the wrong edition's tables give the wrong answer silently.
    """
    try:
        riwayah = to_sdk_slug(request.args.get("riwayah") or DEFAULT_RIWAYAH)
    except UnsupportedRiwayah as exc:
        abort(400, str(exc))
    try:
        model = _seg_config_model(riwayah)
    except EditionsUnavailable as exc:
        # A Hafs-only build serves Hafs and refuses the rest. That is a
        # documented state, not a fault, and the client can act on a 503.
        abort(503, str(exc))
    return orjson_response(
        model.model_dump(**_DUMP),
        # Static config keyed off process restart — minute-long client cache
        # is safe (changing a constant requires a restart anyway).
        headers={"Cache-Control": "private, max-age=60"},
    )


def _seg_config_model(riwayah: str) -> SegConfigResponse:
    return SegConfigResponse(
        seg_font_size=SEG_FONT_SIZE,
        seg_word_spacing=SEG_WORD_SPACING,
        seg_scroll_anim_mode=SEG_SCROLL_ANIM_MODE,
        trim_pad_left=TRIM_PAD_LEFT,
        trim_pad_right=TRIM_PAD_RIGHT,
        trim_dim_alpha=TRIM_DIM_ALPHA,
        low_conf_default_threshold=LOW_CONF_DEFAULT_THRESHOLD,
        validation_categories=list(ALL_CATEGORIES),
        riwayah=from_sdk_slug(riwayah),
        muqattaat_verses=sorted(edition_tables.muqattaat_verses(riwayah)),
        qalqala_letters=sorted(_QALQALA_LETTERS),
        standalone_refs=sorted(edition_tables.standalone_refs(riwayah)),
        standalone_words=sorted(edition_tables.standalone_words(riwayah)),
        muqattaat_words=sorted(edition_tables.muqattaat_words(riwayah)),
        low_confidence_threshold=LOW_CONFIDENCE_THRESHOLDS[riwayah],
        accordion_context=ACCORDION_CONTEXT,
    )


@seg_data_bp.route("/reciters")
def seg_reciters():
    """List reciters tracked in the SQLite state store.

    Joins ``state_service.all_rows()`` (lifecycle + visibility from DB) with the
    in-memory catalog snapshot. ``audio_source`` is the channel
    (``delivery.source`` — ``mp3quran``, ``qul``, etc.); ``audio_category``
    is the by_surah / by_ayah split the FE needs to gate per-row playback
    routing (proxy wrap, clip vs chapter URL). The catalog is cached at boot,
    so this endpoint does zero bucket I/O per request.
    """
    by_slug = {
        d.slug: (d.source, d.audio_category.value) for d in catalog_service.snapshot().deliveries
    }
    model = SegRecitersResponse.model_validate(
        [
            {
                "slug": row.slug,
                "name": slug_to_name(row.slug),
                "audio_source": by_slug.get(row.slug, ("", ""))[0],
                "audio_category": by_slug.get(row.slug, ("", ""))[1],
                "state": row.state.value,
                "visibility": row.visibility.value,
            }
            for row in sorted(state_service.all_rows(), key=lambda r: r.slug)
            if state_service.has_content_access(row.slug)
        ]
    )
    # no-store: this list carries live lifecycle state (state/visibility per
    # row) that changes on claim/release/transition; a client max-age made the
    # Segments reciter list + picker stale for up to 30s after an action.
    return orjson_response(model.model_dump(**_DUMP), headers={"Cache-Control": "no-store"})


@seg_data_bp.route("/chapters/<reciter>")
def seg_chapters(reciter):
    """Return list of chapter numbers available for a reciter."""
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    entries = load_detailed(reciter)
    if not entries:
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    chapters = sorted(set(chapter_from_ref(e["ref"]) for e in entries))
    return jsonify(chapters)


@seg_data_bp.route("/data/<reciter>/<int:chapter>")
def seg_data(reciter, chapter):
    """Return segments, audio URL, summary, and issues for a chapter.

    Shards mutate on re-edit and we don't currently cache-bust this URL, so
    we ride ETag + ``must-revalidate``: the browser always asks the server,
    and the server returns 304 when the encoded body hasn't changed.
    """
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Chapter not found").model_dump(exclude_none=True)), 404
    verse_filter = request.args.get("verse")
    result = get_chapter_data(reciter, chapter, verse_filter)
    if result is None:
        return jsonify(ErrorEnvelope(error="Chapter not found").model_dump(exclude_none=True)), 404
    model = SegDataResponse.model_validate(result)
    return orjson_cached_response(model.model_dump(**_DUMP))


@seg_data_bp.route("/auto-split/<reciter>")
def seg_auto_split_map(reciter):
    """Return the full precomputed Auto Split cursor map for a reciter.

    Read-only bulk sibling of the per-uid ``POST /api/seg/auto-split/<reciter>``
    in ``routes/segments/edit.py``: the FE preloads the whole
    ``{uid: {cursors, refs, kind}}`` map once (on auto-split accordion open) so
    each Auto Split click is a zero-network O(1) lookup instead of a round trip.
    Ungated + cached, matching the sibling read endpoints (``/validate``,
    ``/all``) — the map is no more sensitive than ``/all`` (which already
    exposes every uid + matched_ref). The map content is stable across a
    session (the sidecar is offline-computed; edits only mint new uids that are
    legitimate misses), so ETag revalidation returns 304 on repeat.
    """
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    from services.auto_split import load_auto_split_map

    return orjson_cached_response({"by_uid": load_auto_split_map(reciter)})


def _delivery_riwayah(reciter: str) -> str:
    """The edition this payload's coordinates are in, cross-checked.

    The catalog row is the authority and ``detailed.json`` is the evidence;
    ``sdk_riwayah_for`` raises when they disagree. It is a 409 rather than a
    Hafs default because the frontend keys its script, font, word counts and
    verse totals off this one string — answering "hafs" for a Warsh delivery
    whose catalog row is missing renders Warsh coordinates in DigitalKhatt and
    lets a reviewer save against them.
    """
    try:
        return from_sdk_slug(sdk_riwayah_for(reciter))
    except (RiwayahMismatch, UnsupportedRiwayah) as exc:
        abort(409, str(exc))


@seg_data_bp.route("/all/<reciter>")
def seg_all(reciter):
    """Return all segments across all chapters for a reciter.

    Flag threads are reduced to the FE-facing shape here: author identity is
    redacted to the role only unless the viewer holds
    ``segments.see_flagger_identity``, and each comment carries a ``mine``
    marker so the flagger can edit their own root comment.
    """
    if not state_service.has_content_access(reciter):
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404
    entries = load_detailed(reciter)
    if not entries:
        return jsonify(ErrorEnvelope(error="Reciter not found").model_dump(exclude_none=True)), 404

    # Chapter audio URLs come from the bucket audio_manifest sidecar
    # (catalog/audio_manifest/<slug>.json) — the single source of truth
    # post Migration #5. The legacy per-entry ``audio`` field is no longer
    # written by the extractor and no longer read here.
    from services.audio.audio_meta import chapter_urls

    viewer = current_user()
    viewer_id = getattr(viewer, "hf_user_id", None)
    can_see_flagger = can(viewer, "segments.see_flagger_identity")

    audio_by_chapter: dict[str, str] = dict(chapter_urls(reciter))
    segments = []
    chapter_seg_idx = {}

    for entry_idx, entry in enumerate(entries):
        ch = chapter_from_ref(entry["ref"])
        for seg in entry.get("segments", []):
            idx = chapter_seg_idx.get(ch, 0)
            chapter_seg_idx[ch] = idx + 1
            mref = seg.get("matched_ref", "")
            seg_uid = seg.get("segment_uid") or ""
            if not seg_uid:
                seg_uid = uuid7()
                seg["segment_uid"] = seg_uid
            # `audio_url` deliberately omitted — redundant with the
            # top-level ``audio_by_chapter[chapter]`` map; every FE
            # consumer already falls back to it.
            seg_dict = {
                "chapter": ch,
                "entry_idx": entry_idx,
                "index": idx,
                "segment_uid": seg_uid,
                "time_start": seg.get("time_start", 0),
                "time_end": seg.get("time_end", 0),
                "matched_ref": mref,
                "confidence": round(seg.get("confidence", 0.0), 4),
                "entry_ref": entry.get("ref", ""),
            }
            if seg.get("wrap_word_ranges"):
                seg_dict["wrap_word_ranges"] = seg["wrap_word_ranges"]
            if seg.get("ignored_categories"):
                seg_dict["ignored_categories"] = seg["ignored_categories"]
            elif seg.get("ignored"):
                seg_dict["ignored_categories"] = ["_all"]
            if seg.get("is_wasl"):
                seg_dict["is_wasl"] = True
            if seg.get("flag"):
                seg_dict["flag"] = flag_view(seg["flag"], viewer_id, can_see_flagger)
            if seg.get("word_timings"):
                seg_dict["word_timings"] = seg["word_timings"]
            segments.append(seg_dict)

    pad_left_ms, pad_right_ms, min_silence_floor_ms = resolve_pad(cache.get_seg_meta(reciter))
    # dk_words + verse_word_counts moved off this payload to the immutable
    # ``/api/static/quran-refs.json`` asset (fetched once per browser).
    # Per-audio-URL duration in ms, sourced from the audio_manifest sidecar.
    # Surfaced so the FE can clamp trim/adjust right-pad and split viewEnd
    # against actual audio EOF — extraction sometimes leaves the last seg's
    # time_end past the actual audio end, which (without clamping) makes the
    # waveform fetch return truncated/empty peaks and the canvas paints blank.
    # URL-keyed (not chapter-keyed) so by_ayah deliveries — where each ayah
    # is its own audio file — are clamped correctly per-ayah.
    duration_ms_by_url: dict[str, int] = {}
    for key, url in (chapter_urls(reciter) or {}).items():
        meta = chapter_meta(reciter, key)
        if not isinstance(meta, dict):
            continue
        duration_sec = meta.get("duration_sec")
        if (
            isinstance(url, str)
            and url
            and isinstance(duration_sec, (int, float))
            and duration_sec > 0
        ):
            duration_ms_by_url[url] = int(duration_sec * 1000)
    # Per-chapter map for FE convenience (when seg.audio_url isn't set the
    # FE falls back to audio_by_chapter[chapter]; mirror the same shape here).
    chapter_duration_ms_by_chapter: dict[str, int] = {}
    for ch_str, url in audio_by_chapter.items():
        if url in duration_ms_by_url:
            chapter_duration_ms_by_chapter[ch_str] = duration_ms_by_url[url]
    # Served directly without a SegAllResponse validate+dump round-trip. The dict
    # assembled here is already the exact on-wire shape (keys in field order,
    # optionals emitted only-when-truthy, confidence pre-rounded, flags carrying
    # their intentional ``null`` leaves), so the round-trip would be pure overhead
    # — hundreds of ms on a full-mushaf reciter, and uncached. Conformance to
    # SegAllResponse is asserted at the test boundary (test_wire_seg_models) and
    # the bytes frozen by test_seg_all_snapshot; bucket data is validated at write.
    payload = {
        "riwayah": _delivery_riwayah(reciter),
        "segments": segments,
        "audio_by_chapter": audio_by_chapter,
        "chapter_duration_ms_by_chapter": chapter_duration_ms_by_chapter,
        "duration_ms_by_url": duration_ms_by_url,
        "reciter_vbr_chapters": vbr_chapters_for_reciter(reciter),
        # Symmetric shim: total padding == 2 * pad_ms ≈ pad_left + pad_right.
        "pad_ms": (pad_left_ms + pad_right_ms) // 2,
        "pad_left_ms": pad_left_ms,
        "pad_right_ms": pad_right_ms,
        "min_silence_floor_ms": min_silence_floor_ms,
    }
    return orjson_response(payload)
