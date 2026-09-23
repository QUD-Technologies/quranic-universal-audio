"""Query helpers for Segments-tab read-only data endpoints.

No Flask imports -- functions accept parameters and return plain dicts/lists.
"""

import statistics

from config import LOW_CONFIDENCE_RED, LOW_CONFIDENCE_THRESHOLDS
from services.audio.audio_meta import (
    chapter_bitrate_kbps_for_reciter,
    is_vbr,
    vbr_chapters_for_reciter,
)
from services.reference.delivery_edition import sdk_riwayah_for
from services.storage import cache
from services.storage.data_loader import (
    get_word_counts,
    load_detailed,
    resolve_pad,
)
from utils.references import chapter_from_ref


def get_chapter_data(reciter: str, chapter: int, verse_filter: str | None = None) -> dict | None:
    """Return segments, audio URL, summary, and issues for a chapter.

    Returns ``None`` if no matching entries exist for the ``(reciter, chapter)``
    pair — the caller converts to an HTTP 404. Otherwise returns the full
    response dict used by ``GET /api/seg/data/<reciter>/<chapter>``.
    """
    entries = load_detailed(reciter)
    matching = [e for e in entries if chapter_from_ref(e["ref"]) == chapter]
    if not matching:
        return None

    # Chapter audio URL sourced from the audio_manifest sidecar
    # (``catalog/audio_manifest/<slug>.json::chapters[ch].url``) — the
    # single source of truth post Migration #5. Returns "" when the sidecar
    # has no entry for the chapter; the FE handles the missing-URL case.
    from services.audio.audio_meta import chapter_urls

    audio_url = chapter_urls(reciter).get(str(chapter), "")

    segments = []
    idx = 0
    for entry_idx, entry in enumerate(matching):
        for seg in entry.get("segments", []):
            t_start = seg.get("time_start", 0)
            t_end = seg.get("time_end", 0)
            mref = seg.get("matched_ref", "")
            seg_dict = {
                "index": idx,
                "entry_idx": entry_idx,
                "time_start": t_start,
                "time_end": t_end,
                "matched_ref": mref,
                "confidence": round(seg.get("confidence", 0.0), 4),
                "audio_url": audio_url,
            }
            if seg.get("ignored_categories"):
                seg_dict["ignored_categories"] = seg["ignored_categories"]
            elif seg.get("ignored"):
                seg_dict["ignored_categories"] = ["_all"]
            if seg.get("is_wasl"):
                seg_dict["is_wasl"] = True
            if seg.get("word_timings"):
                seg_dict["word_timings"] = seg["word_timings"]
            segments.append(seg_dict)
            idx += 1

    if verse_filter:
        prefix = f"{chapter}:{verse_filter}:"
        segments = [s for s in segments if s["matched_ref"].startswith(prefix)]

    matched = [s for s in segments if s["matched_ref"]]
    failed = [s for s in segments if not s["matched_ref"]]
    confidences = [s["confidence"] for s in matched]

    speech_durations = [s["time_end"] - s["time_start"] for s in segments]
    total_speech = sum(speech_durations)
    pad_left_ms, pad_right_ms, _floor = resolve_pad(cache.get_seg_meta(reciter))
    silence_durations = []
    for i in range(len(segments) - 1):
        if segments[i]["entry_idx"] == segments[i + 1]["entry_idx"]:
            gap = (
                segments[i + 1]["time_start"] - segments[i]["time_end"] + pad_left_ms + pad_right_ms
            )
            if gap > 0:
                silence_durations.append(gap)
    total_silence = sum(silence_durations)

    issue_indices = []
    for s in segments:
        if not s["matched_ref"]:
            issue_indices.append(s["index"])
        elif s["confidence"] < LOW_CONFIDENCE_RED:
            issue_indices.append(s["index"])

    # Missing verses
    missing_verses = []
    # The chapter's verse list under THIS edition's counting profile: Warsh's
    # al-Baqarah ends at 285, so a Hafs-derived expectation would report 2:286
    # missing on every Warsh delivery, forever.
    riwayah = sdk_riwayah_for(reciter)
    wc = get_word_counts(riwayah)
    expected_verses = {v for (s, v) in wc if s == chapter}
    if expected_verses:
        found_verses = set()
        for s in segments:
            ref = s["matched_ref"]
            if ref:
                parts = ref.split("-")
                if len(parts) == 2:
                    start = parts[0].split(":")
                    end = parts[1].split(":")
                    if len(start) >= 2:
                        try:
                            for v in range(int(start[1]), int(end[1]) + 1):
                                found_verses.add(v)
                        except (ValueError, IndexError):
                            pass
        missing_verses = sorted(expected_verses - found_verses)

    summary = {
        "total_segments": len(segments),
        "matched_segments": len(matched),
        "failed_segments": len(failed),
        "conf_min": round(min(confidences), 4) if confidences else 0,
        "conf_median": round(statistics.median(confidences), 4) if confidences else 0,
        "conf_mean": round(statistics.mean(confidences), 4) if confidences else 0,
        "conf_max": round(max(confidences), 4) if confidences else 0,
        "below_60": sum(1 for c in confidences if c < LOW_CONFIDENCE_RED),
        # The same per-edition cutoff validation and the ready-gate use, so the
        # stats panel and the accordion cannot disagree about one segment.
        "below_80": sum(1 for c in confidences if c < LOW_CONFIDENCE_THRESHOLDS[riwayah]),
        "total_speech_ms": round(total_speech),
        "avg_segment_ms": round(total_speech / len(segments)) if segments else 0,
        "total_silence_ms": round(total_silence),
        "avg_silence_ms": round(total_silence / len(silence_durations)) if silence_durations else 0,
        "issue_indices": issue_indices,
        "missing_verses": [f"{chapter}:{v}" for v in missing_verses],
    }

    # dk_words + verse_word_counts moved off per-request payloads to the
    # immutable ``/api/static/quran-refs.json`` asset (fetched once per browser).
    return {
        "audio_url": audio_url,
        "vbr": is_vbr(reciter, chapter),
        "reciter_vbr_chapters": vbr_chapters_for_reciter(reciter),
        # Sparse {chapter -> kbps} for CBR chapters with a positive bitrate.
        # Consumed by the segments-tab audio-warmup util to compute the byte
        # offset of a seg's `time_start` and warm a 64 KB Range ahead of
        # play. Absence ⇒ "don't warm" (VBR, missing kbps, or unknown mode).
        "chapter_bitrate_kbps": chapter_bitrate_kbps_for_reciter(reciter),
        "segments": segments,
        "summary": summary,
    }
