"""Readings summary: what a Hafs delivery reads wherever the riwayah allows a choice.

``build(slug)`` folds the delivery's v15 shards into ``TsReadingsDoc`` rows: one per shown
selector and word (``istifham_article`` splits into its three words), each listing every option
with the verses read that way. A shard carries a variant only where its condition held
(stopped, joined or started there), so waqf/wasl/ibtidaa rows appear only where they applied.
Picks made without evidence (``by: "default"``) and selectors outside ``SHOWN`` are dropped.
Each verse carries the start of the part the option was read in (``start_ms``), so a verse
recited twice — stopped, then again joined — jumps to the rendition that read that way.

The build reads its shards in parallel and decodes only ``_meta`` and the readings that carry
``variants`` (``variant_view``), not the whole shard.

``refresh(slug)`` builds and writes ``reciters/<slug>/readings.json``; the aligner timestamps
run, the ``ts-refreshed`` notice and the ``admin_readings.py`` backfill call it. ``doc(slug)``
serves the stored summary, building it on first request when it is missing or of an older
``schema_version``; builds of one slug
are single-flight. A non-Hafs delivery always gets an empty summary.
"""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import orjson
from pydantic import ValidationError

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH, UnsupportedRiwayah, resolve_sdk_slug
from qua_shared.schemas import TsReadingOption, TsReadingRow, TsReadingsDoc, TsReadingVerse
from qua_shared.schemas.bucket.ts_readings import READINGS_SCHEMA_VERSION
from services.state import catalog as catalog_service
from services.state import state as state_service
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

_READ_WORKERS = 8

# Shards are compact pydantic dumps: ``_meta`` first, each reading opening with its ``id`` then
# ``parts``, ``variants`` its last key and ``readings`` the document's last key.
_META_OPEN = b'{"_meta":'
_READINGS_OPEN = b',"readings":['
_READING_ID = b'{"id":"'
_READING_OPEN = re.compile(rb'\{"id":"[^"]*","parts":')
_VARIANTS_KEY = b'"variants":'

_cache: dict[str, TsReadingsDoc] = {}
_slug_locks: dict[str, threading.Lock] = {}
_lock = threading.Lock()


def is_hafs(slug: str) -> bool:
    delivery = catalog_service.find_delivery(slug)
    if delivery is None:
        return False
    try:
        return resolve_sdk_slug(delivery.riwayah) == DEFAULT_SDK_RIWAYAH
    except UnsupportedRiwayah:
        return False


def released_hafs_slugs() -> list[str]:
    return [
        row.slug
        for row in state_service.all_rows()
        if row.state.value == "released" and is_hafs(row.slug)
    ]


def variant_view(body: bytes) -> dict:
    """A shard's ``_meta`` and only its readings carrying ``variants``; any other layout whole."""
    try:
        return _variant_slices(body)
    except ValueError:
        return orjson.loads(body)


def _variant_slices(body: bytes) -> dict:
    if not body.startswith(_META_OPEN):
        raise ValueError("shard does not open with _meta")
    first = _READING_OPEN.search(body)
    meta_end = body.rfind(_READINGS_OPEN, 0, first.start() if first else len(body))
    if meta_end < 0:
        raise ValueError("shard has no readings list")
    readings = []
    pos = meta_end
    while (key := body.find(_VARIANTS_KEY, pos)) >= 0:
        start = _reading_start(body, key)
        following = _READING_OPEN.search(body, key)
        end = following.start() - 1 if following else len(body) - 2  # drop "," or "]}"
        readings.append(orjson.loads(body[start:end]))
        pos = end
    return {"_meta": orjson.loads(body[len(_META_OPEN) : meta_end]), "readings": readings}


def _reading_start(body: bytes, before: int) -> int:
    start = body.rfind(_READING_ID, 0, before)
    while start >= 0 and not _READING_OPEN.match(body, start):
        start = body.rfind(_READING_ID, 0, start)
    if start < 0:
        raise ValueError("variants outside a reading")
    return start


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
                    "start_ms": _part_start(reading.get("parts") or [], ids[0]),
                    "selector": sel,
                    "option": variant["chosen"],
                    "options": list((catalogue.get(sel) or {}).get("options") or []),
                    "refs": [words[i][0] for i in ids],
                    "texts": [words[i][1] for i in ids],
                }
            )
    return hits


def _part_start(parts: list, word: int) -> int | None:
    """Start ms of the part (``[ref, start, end, first, count]``) holding word index ``word``."""
    for _ref, start, _end, first, count in parts:
        if first <= word < first + count:
            return start
    return None


def _ref_key(ref: str) -> tuple[int, ...]:
    return tuple(int(part) for part in ref.split(":"))


def _verse(hit: dict) -> TsReadingVerse:
    refs = hit["refs"]
    surah, ayah = _ref_key(refs[0])[:2]
    last = _ref_key(refs[-1])[1]
    label = f"{surah}:{ayah}–{last}" if last != ayah else f"{surah}:{ayah}"
    return TsReadingVerse(surah=surah, ayah=ayah, label=label, start_ms=hit["start_ms"])


def _row_key(hit: dict) -> str:
    form = _WORD_FORMS.get(hit["selector"], {}).get(hit["refs"][0])
    return f"{hit['selector']}/{form}" if form else hit["selector"]


def fold(hits: list[dict]) -> list[TsReadingRow]:
    """Rows in mushaf order; each option's verses deduped (first rendition kept) and sorted."""
    rows: dict[str, dict] = {}
    for hit in sorted(hits, key=lambda h: (_ref_key(h["refs"][0]), h["start_ms"] or 0)):
        row = rows.setdefault(_row_key(hit), {"hit": hit, "options": {}})
        for option in hit["options"]:
            row["options"].setdefault(option, {})
        verse = _verse(hit)
        row["options"].setdefault(hit["option"], {}).setdefault(verse.label, verse)
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
    with ThreadPoolExecutor(max_workers=_READ_WORKERS, thread_name_prefix="readings") as pool:
        bodies = pool.map(lambda chapter: data_dir.read_timestamps_chapter(slug, chapter), CHAPTERS)
        hits = [
            hit for body in bodies if body is not None for hit in shard_hits(variant_view(body))
        ]
    return TsReadingsDoc(slug=slug, built_at=built_at, rows=fold(hits))


def _slug_lock(slug: str) -> threading.Lock:
    with _lock:
        return _slug_locks.setdefault(slug, threading.Lock())


def refresh(slug: str) -> TsReadingsDoc:
    """Build the summary and store it; a failed write still serves the built summary."""
    with _slug_lock(slug):
        return _refresh(slug)


def _refresh(slug: str) -> TsReadingsDoc:
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


def stored_doc(slug: str) -> TsReadingsDoc | None:
    """The stored summary, or ``None`` when it is missing, invalid or of an older schema."""
    try:
        stored = TsReadingsDoc.model_validate(
            get_backend().read_json(storage_paths.readings_path(slug))
        )
    except (StorageNotFound, ValidationError):
        return None
    return stored if stored.schema_version == READINGS_SCHEMA_VERSION else None


def doc(slug: str) -> TsReadingsDoc:
    """The stored summary, built and stored on first request when it is missing."""
    with _lock:
        cached = _cache.get(slug)
    if cached is not None:
        return cached
    if not is_hafs(slug):
        return TsReadingsDoc(slug=slug)
    with _slug_lock(slug):
        with _lock:
            cached = _cache.get(slug)
        if cached is not None:
            return cached
        stored = stored_doc(slug)
        if stored is None:
            return _refresh(slug)
        with _lock:
            _cache[slug] = stored
        return stored


def invalidate() -> None:
    with _lock:
        _cache.clear()
