"""Keep the audio manifest + delivery rollup true to the audio the pipeline made.

A playlist source cannot be HTTP-probed (a YouTube watch page is not an mp3),
so its manifest is minted with URLs only. Once acquire has persisted the
canonical encode (192 kbps CBR, 44.1 kHz) and split has cut the combined files,
the real per-chapter size / duration and the chapter's offset inside its source
are known — this module writes them back. Fields a CDN probe already filled are
never overwritten.

It also applies the split's coverage outcome: chapters a combined file turned
out not to hold are dropped, chapters it held beyond the plan are adopted.
"""

from __future__ import annotations

import logging

from qua_shared.schemas import Actor, AudioManifestSidecar, Role
from services.audio import audio_meta
from services.storage import cache, storage_paths
from services.storage.hf_bucket import get_backend

log = logging.getLogger("inspector")

CANONICAL_KBPS = 192
CANONICAL_SAMPLE_RATE = 44100
_MS = 1000
_PIPELINE_ACTOR = Actor(hf_user_id="SYSTEM_ACTOR", login_at_time="align_pipeline", role=Role.OWNER)


def _read(slug: str) -> dict:
    return get_backend().read_json(storage_paths.audio_manifest_path(slug))


def _write(slug: str, doc: dict) -> None:
    chapters = doc.get("chapters") or {}
    meta = dict(doc.get("_meta") or {})
    meta["chapter_count"] = len(chapters)
    meta["checksum"] = audio_meta.manifest_checksum(chapters)
    doc = {**doc, "chapters": chapters, "_meta": meta}
    model = AudioManifestSidecar.model_validate(doc)
    get_backend().write_json_atomic(
        storage_paths.audio_manifest_path(slug), model.model_dump(mode="json", by_alias=True)
    )
    cache.pop_audio_manifest_cache(slug)


def record_acquired(slug: str, outcomes: dict[str, dict]) -> None:
    """Fill unprobed chapter metadata from the acquire report's outcomes."""
    doc = _read(slug)
    changed = False
    for key, entry in (doc.get("chapters") or {}).items():
        out = outcomes.get(str(key))
        if out and _fill(entry, out):
            changed = True
    if changed:
        _write(slug, doc)
    sync_delivery(slug)


def apply_split(
    slug: str,
    *,
    cuts: dict[int, dict],
    dropped: list[int],
    adopted: dict[int, str],
) -> None:
    """``cuts``: ``{chapter: {offset_ms, bytes, duration_ms}}`` from the split job;
    ``adopted``: ``{chapter: source url}`` for chapters found beyond the plan."""
    from .sources import bucket_chapter_url

    doc = _read(slug)
    chapters = doc.setdefault("chapters", {})
    for ch in dropped:
        chapters.pop(str(ch), None)
    for ch, url in adopted.items():
        chapters[str(ch)] = {"url": bucket_chapter_url(slug, ch), "source_url": url}
    for ch, cut in cuts.items():
        entry = chapters.get(str(ch))
        if entry is None:
            continue
        entry["source_offset_ms"] = int(cut["offset_ms"])
        for field in ("size_bytes", "duration_sec", "bitrate_kbps", "bitrate_mode"):
            entry.pop(field, None)
        _fill(entry, cut)
    doc["chapters"] = dict(sorted(chapters.items(), key=lambda kv: int(kv[0])))
    _write(slug, doc)
    sync_delivery(slug)


def _fill(entry: dict, outcome: dict) -> bool:
    fills = {
        "size_bytes": outcome.get("bytes"),
        "duration_sec": (
            round(outcome["duration_ms"] / _MS) if outcome.get("duration_ms") is not None else None
        ),
        "bitrate_kbps": CANONICAL_KBPS,
        "bitrate_mode": "cbr",
    }
    changed = False
    for field, value in fills.items():
        if value is not None and entry.get(field) is None:
            entry[field] = value
            changed = True
    return changed


def sync_delivery(slug: str) -> None:
    """Chapter count + the audio rollup, for fields the catalog does not know yet."""
    from services.state import catalog as catalog_service

    delivery = catalog_service.find_delivery(slug)
    chapters = audio_meta.manifest_chapters(slug)
    if delivery is None or not chapters:
        return
    durations = [e.get("duration_sec") for e in chapters.values()]
    fields: dict[str, object] = {}
    if delivery.chapter_count != len(chapters):
        fields["chapter_count"] = len(chapters)
    if delivery.total_duration_sec is None and all(d is not None for d in durations):
        fields["total_duration_sec"] = int(sum(durations))
    if delivery.bitrate_mode.value == "unknown" and all(
        e.get("bitrate_mode") == "cbr" and e.get("bitrate_kbps") == CANONICAL_KBPS
        for e in chapters.values()
    ):
        fields["bitrate_mode"] = "cbr"
        fields["bitrate_kbps_nominal"] = CANONICAL_KBPS
    if delivery.sample_rate_hz is None and fields.get("bitrate_mode") == "cbr":
        fields["sample_rate_hz"] = CANONICAL_SAMPLE_RATE
    if not fields:
        return
    try:
        catalog_service.edit_delivery_fields(
            actor=_PIPELINE_ACTOR, slug=slug, fields=fields, reason="align pipeline audio probe"
        )
    except Exception as exc:  # noqa: BLE001 — metadata only; never fails the run
        log.warning("align: delivery rollup for %s not updated: %s", slug, exc)
