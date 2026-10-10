"""Stage 3 — review sidecars, computed reciter-wide on the aligner Space.

Builds every chapter's ``ChapterCandidate`` from the staged aligner results (the
same adaptation assemble uses, so the sidecars index exactly the rows that get
published) and streams ``POST /api/v1/extraction/sidecars``. Auto Split reuses
the align stage's candidate-only interactive timings; Low Confidence is the review policy
over the neural timing head's checks, which the align stage returned per row
(``review_checks``, sent on the staged timeline); the Space times only the segments without
them. The Space runs one sidecar job at a
time; a 409 waits and retries. ``verse_ends_v1`` and a matcher-lattice ``missed_waqf_v2``
(Low Confidence Waqf) are built here from the staged rows' lattice pauses, word timings and
the chapters' baked loudness levels, without the Space (``pause_sidecar``); on a Hafs
delivery the Space's neural Low Confidence Waqf is merged into it (``waqf_union``). A segment
gets one card: one in Low Confidence is dropped from Low Confidence Waqf, its fit being too
poor to trust a stop inside it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from services.storage.hf_bucket import resolve_bucket_repo

from . import adapt, pause_sidecar, progress, staging, waqf_union
from .aligner_client import AlignerClient, AlignerError
from .params import AUTO_SPLIT_TIMING_SOURCE, AlignParams

log = logging.getLogger("inspector")

LOW_CONFIDENCE_FILE = "low_confidence_v2.json"
AUTO_SPLIT_FILE = "auto_split_v1.json"
MISSED_WAQF_FILE = pause_sidecar.SIDECAR_FILE
VERSE_ENDS_FILE = pause_sidecar.VERSE_ENDS_FILE
BUSY_RETRY_S = 60
BUSY_MAX_WAIT_S = 6 * 3600
TRANSIENT_ATTEMPTS = 3
#: Both sides round region seconds to ms.
SPAN_TOLERANCE_MS = 1


class SidecarsStageError(RuntimeError):
    pass


@dataclass
class Payloads:
    """The sidecars request's per-chapter parts, by published segment index."""

    candidates: dict[str, dict]
    #: The align stage's word timings, ``None`` when a chapter was staged without them.
    timings: dict[str, list[list[dict] | None]] | None
    #: The align stage's review checks, ``None`` when no row carries one.
    checks: dict[str, list[dict | None]] | None


def payloads_for(
    slug: str, run_id: str, chapters: list[int], sources: dict[int, str], riwayah: str
) -> Payloads:
    docs = staging.read_chapters(slug, run_id, chapters)
    candidates: dict[str, dict] = {}
    timings: dict[str, list[list[dict] | None]] = {}
    checks: dict[str, list[dict | None]] = {}
    for ch in chapters:
        candidate, _events, _basmala = adapt.adapt_chapter(
            ch, docs[ch], source_url=sources[ch], riwayah=riwayah
        )
        candidates[str(ch)] = candidate
        kept_rows = [row for row in docs[ch].get("segments", []) if not adapt.is_special(row)]
        timings[str(ch)] = [
            row.get("words") if isinstance(row.get("words"), list) else None for row in kept_rows
        ]
        checks[str(ch)] = [_rebased_checks(row) for row in kept_rows]
    current = all(
        (docs[ch].get("_inspector") or {}).get("auto_split_timing_source")
        == AUTO_SPLIT_TIMING_SOURCE
        for ch in chapters
    )
    any_checks = any(c is not None for rows in checks.values() for c in rows)
    return Payloads(candidates, timings if current else None, checks if any_checks else None)


def _rebased_checks(row: dict) -> dict | None:
    """The row's review checks on its staged timeline (the split stage may have moved the
    row onto its chapter's cut), or ``None`` when it has none for this span."""
    item = row.get("review_checks")
    if not isinstance(item, dict) or not isinstance(check := item.get("row"), dict):
        return None
    try:
        start, end = int(check["start_ms"]), int(check["end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    row_start, row_end = adapt.to_ms(row["time_from"]), adapt.to_ms(row["time_to"])
    if abs((end - start) - (row_end - row_start)) > SPAN_TOLERANCE_MS:
        return None
    shift = row_start - start
    return {
        "row": {**check, "start_ms": start + shift, "end_ms": end + shift},
        "joins": [
            {**join, "at_ms": int(join["at_ms"]) + shift}
            for join in item.get("joins") or []
            if isinstance(join, dict) and "at_ms" in join
        ],
    }


def candidates_for(
    slug: str, run_id: str, chapters: list[int], sources: dict[int, str], riwayah: str
) -> dict[str, dict]:
    """Compatibility view for callers that only need the staged candidates."""
    return payloads_for(slug, run_id, chapters, sources, riwayah).candidates


def run(
    slug: str,
    run_id: str,
    params: AlignParams,
    chapters: list[int],
    sources: dict[int, str],
) -> None:
    _stage_missed_waqf(slug, run_id, params, chapters, sources)
    if staging.read_json(staging.sidecar_path(slug, run_id, AUTO_SPLIT_FILE)) is None:
        _stage_space_sidecars(slug, run_id, params, chapters, sources)
    else:
        log.info("align %s: sidecars already staged, skipped", run_id)
    _one_card(slug, run_id)


def _stage_space_sidecars(
    slug: str, run_id: str, params: AlignParams, chapters: list[int], sources: dict[int, str]
) -> None:
    payloads = payloads_for(slug, run_id, chapters, sources, params.riwayah)
    body = {
        "slug": slug,
        "riwayah": params.riwayah,
        "audio": {
            "repo": resolve_bucket_repo(),
            "path_tpl": f"reciters/{slug}/audio/{{chapter}}.mp3",
        },
        "candidates": payloads.candidates,
        **({"auto_split_timings": payloads.timings} if payloads.timings is not None else {}),
        **({"review_checks": payloads.checks} if payloads.checks is not None else {}),
    }
    result = _call(run_id, body)
    # A non-Hafs delivery gets no neural review (D12 — the Space answers ``null``):
    # its Low Confidence Waqf is the matcher-lattice one alone.
    if result.get("low_confidence_v2") is not None:
        staging.write_json(
            staging.sidecar_path(slug, run_id, LOW_CONFIDENCE_FILE), result["low_confidence_v2"]
        )
    if result.get("missed_waqf_v2") is not None:
        path = staging.sidecar_path(slug, run_id, MISSED_WAQF_FILE)
        staging.write_json(
            path, waqf_union.merge(result["missed_waqf_v2"], staging.read_json(path))
        )
    staging.write_json(staging.sidecar_path(slug, run_id, AUTO_SPLIT_FILE), result["auto_split_v1"])
    log.info("align %s: sidecars staged", run_id)


def _one_card(slug: str, run_id: str) -> None:
    """Drop the Low Confidence Waqf items of segments already in Low Confidence."""
    low = staging.read_json(staging.sidecar_path(slug, run_id, LOW_CONFIDENCE_FILE)) or {}
    path = staging.sidecar_path(slug, run_id, MISSED_WAQF_FILE)
    missed = staging.read_json(path) or {}
    flagged = set(low.get("failures") or []) & set(missed.get("by_uid") or {})
    if not flagged:
        return
    by_uid = {uid: item for uid, item in missed["by_uid"].items() if uid not in flagged}
    meta = {**missed.get("_meta", {}), "segments": len(by_uid), "low_confidence": len(flagged)}
    staging.write_json(path, {**missed, "_meta": meta, "by_uid": by_uid})
    log.info(
        "align %s: %d Low Confidence Waqf item(s) left to Low Confidence", run_id, len(flagged)
    )


def _stage_missed_waqf(
    slug: str, run_id: str, params: AlignParams, chapters: list[int], sources: dict[int, str]
) -> None:
    path = staging.sidecar_path(slug, run_id, MISSED_WAQF_FILE)
    if staging.read_json(path) is not None:
        return
    docs = staging.read_chapters(slug, run_id, chapters)
    from services.storage.data_loader import load_detailed

    doc, verse_ends = pause_sidecar.build(slug, docs, sources, params.riwayah, load_detailed(slug))
    staging.write_json(staging.sidecar_path(slug, run_id, VERSE_ENDS_FILE), verse_ends)
    staging.write_json(path, doc)
    log.info(
        "align %s: %s staged, %d item(s); %d segment(s) with verse-end verdicts",
        run_id, MISSED_WAQF_FILE, len(doc["by_uid"]), len(verse_ends["by_uid"]),
    )  # fmt: skip


def _call(run_id: str, body: dict) -> dict:
    client = AlignerClient()
    waited = 0
    transient = 0

    def on_progress(ev: dict) -> None:
        progress.set_detail(run_id, sidecar_stage=ev.get("stage"))

    while True:
        progress.check_cancel(run_id)
        try:
            return client.sidecars(body, on_progress)
        except AlignerError as exc:
            if exc.status == 409 and waited < BUSY_MAX_WAIT_S:
                progress.set_detail(run_id, sidecar_stage="waiting_for_space")
                time.sleep(BUSY_RETRY_S)
                waited += BUSY_RETRY_S
                continue
            raise SidecarsStageError(str(exc)) from exc
        except (requests.ConnectionError, requests.Timeout) as exc:
            transient += 1
            if transient >= TRANSIENT_ATTEMPTS:
                raise SidecarsStageError(f"transport: {exc}") from exc
            log.warning("align %s: sidecars transport error, retrying: %s", run_id, exc)
            time.sleep(BUSY_RETRY_S)
