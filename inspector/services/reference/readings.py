"""Readings summary: what a Hafs delivery reads wherever the riwayah allows a choice.

``build(slug)`` folds the delivery's v15 shards into ``TsReadingsDoc`` rows: one per shown
selector and word (``istifham_article`` splits into its three words), each listing every option
with the verses read that way. A shard carries a variant only where its condition held
(stopped, joined or started there), so waqf/wasl/ibtidaa rows appear only where they applied.
Picks made without evidence (``by: "default"``) and selectors outside ``SHOWN`` are dropped.

``refresh(slug)`` builds and writes ``reciters/<slug>/readings.json``; the aligner timestamps
run and the ``ts-refreshed`` notice call it after new shards land. ``doc(slug)`` serves the
stored summary, building it on first request when it is missing. A non-Hafs delivery always
gets an empty summary.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from pydantic import ValidationError

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH, UnsupportedRiwayah, resolve_sdk_slug
from qua_shared.schemas import TsReadingOption, TsReadingRow, TsReadingsDoc, TsReadingVerse
from services.state import catalog as catalog_service
from services.storage import data_dir, storage_paths
from services.storage.hf_bucket import StorageError, StorageNotFound, get_backend

log = logging.getLogger("inspector")

MIN_SHARD_SCHEMA = 15

SHOWN = frozenset(
    {
        "istifham_article",
        "daaf_haraka",
        "yabsut",
        "bastah",
        "almusaytirun",
        "bimusaytir",
        "noon_wasl",
        "yaseen_wasl",
        "irkab_maana",
        "yalhath_dhalik",
        "maliyah_halak",
        "iwaja_qayyima",
        "man_raq",
        "bal_ran",
        "yaa_aatani_waqf",
        "salasila_waqf",
        "alism_ibtidaa",
    }
)

# Chapters holding at least one shown selector: the only shards a summary reads.
CHAPTERS = (2, 6, 7, 10, 11, 18, 27, 30, 36, 49, 52, 68, 69, 75, 76, 83, 88)

# One row per word for the selector whose occurrences are three different words.
_WORD_FORMS = {
    "istifham_article": {
        "6:143:10": "aldhakarayn",
        "6:144:8": "aldhakarayn",
        "10:51:7": "alaan",
        "10:91:1": "alaan",
        "10:59:14": "allah",
        "27:59:9": "allah",
    },
}

_cache: dict[str, TsReadingsDoc] = {}
_lock = threading.Lock()


def is_hafs(slug: str) -> bool:
    delivery = catalog_service.find_delivery(slug)
    if delivery is None:
        return False
    try:
        return resolve_sdk_slug(delivery.riwayah) == DEFAULT_SDK_RIWAYAH
    except UnsupportedRiwayah:
        return False


def shard_hits(shard: dict) -> list[dict]:
    """Shown, evidenced occurrences of one shard: selector, option, word refs and texts."""
    hits = []
    if (shard.get("_meta") or {}).get("schema_version", 0) < MIN_SHARD_SCHEMA:
        return hits
    catalogue = (shard.get("_meta") or {}).get("variant_catalogue") or {}
    for reading in shard.get("readings") or []:
        words = (reading.get("render") or {}).get("w") or []
        for variant in reading.get("variants") or []:
            sel = variant.get("id")
            if sel not in SHOWN or variant.get("by") == "default":
                continue
            ids = variant.get("words") or []
            if not ids or any(i >= len(words) for i in ids):
                continue
            hits.append(
                {
                    "selector": sel,
                    "option": variant["chosen"],
                    "options": list((catalogue.get(sel) or {}).get("options") or []),
                    "refs": [words[i][0] for i in ids],
                    "texts": [words[i][1] for i in ids],
                }
            )
    return hits


def _ref_key(ref: str) -> tuple[int, ...]:
    return tuple(int(part) for part in ref.split(":"))


def _verse(refs: list[str]) -> TsReadingVerse:
    surah, ayah = _ref_key(refs[0])[:2]
    last = _ref_key(refs[-1])[1]
    label = f"{surah}:{ayah}–{last}" if last != ayah else f"{surah}:{ayah}"
    return TsReadingVerse(surah=surah, ayah=ayah, label=label)


def _row_key(hit: dict) -> str:
    form = _WORD_FORMS.get(hit["selector"], {}).get(hit["refs"][0])
    return f"{hit['selector']}/{form}" if form else hit["selector"]


def fold(hits: list[dict]) -> list[TsReadingRow]:
    """Rows in mushaf order; each option's verses deduped and sorted."""
    rows: dict[str, dict] = {}
    for hit in sorted(hits, key=lambda h: _ref_key(h["refs"][0])):
        row = rows.setdefault(_row_key(hit), {"hit": hit, "options": {}})
        for option in hit["options"]:
            row["options"].setdefault(option, {})
        verse = _verse(hit["refs"])
        row["options"].setdefault(hit["option"], {})[verse.label] = verse
    return [
        TsReadingRow(
            selector=row["hit"]["selector"],
            key=key,
            texts=row["hit"]["texts"],
            options=[
                TsReadingOption(
                    option=option,
                    verses=sorted(verses.values(), key=lambda v: (v.surah, v.ayah)),
                )
                for option, verses in row["options"].items()
            ],
        )
        for key, row in rows.items()
    ]


def build(slug: str) -> TsReadingsDoc:
    """The summary from the delivery's shards; empty for a non-Hafs delivery."""
    built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not is_hafs(slug):
        return TsReadingsDoc(slug=slug, built_at=built_at)
    hits: list[dict] = []
    for chapter in CHAPTERS:
        body = data_dir.read_timestamps_chapter(slug, chapter)
        if body is None:
            continue
        hits.extend(shard_hits(json.loads(body)))
    return TsReadingsDoc(slug=slug, built_at=built_at, rows=fold(hits))


def refresh(slug: str) -> TsReadingsDoc:
    """Build the summary and store it; a failed write still serves the built summary."""
    summary = build(slug)
    try:
        get_backend().write_json_atomic(
            storage_paths.readings_path(slug), summary.model_dump(mode="json")
        )
    except StorageError as exc:
        log.warning("readings: %s not stored: %s", slug, exc)
    with _lock:
        _cache[slug] = summary
    log.info("readings: %s rebuilt (%d rows)", slug, len(summary.rows))
    return summary


def refresh_quietly(slug: str) -> None:
    """``refresh`` for callers that must not fail on it (runs, notices)."""
    try:
        refresh(slug)
    except Exception:  # noqa: BLE001 — the summary is derived; its next request rebuilds it
        log.exception("readings: refresh of %s failed", slug)


def refresh_in_background(slug: str) -> None:
    threading.Thread(
        target=refresh_quietly, args=(slug,), name=f"readings-{slug}", daemon=True
    ).start()


def doc(slug: str) -> TsReadingsDoc:
    """The stored summary, built and stored on first request when it is missing."""
    with _lock:
        cached = _cache.get(slug)
    if cached is not None:
        return cached
    if not is_hafs(slug):
        return TsReadingsDoc(slug=slug)
    try:
        stored = TsReadingsDoc.model_validate(
            get_backend().read_json(storage_paths.readings_path(slug))
        )
    except (StorageNotFound, ValidationError):
        return refresh(slug)
    with _lock:
        _cache[slug] = stored
    return stored


def invalidate() -> None:
    with _lock:
        _cache.clear()
