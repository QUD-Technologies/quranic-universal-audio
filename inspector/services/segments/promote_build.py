"""Build the published ``reciters/<slug>/`` artifacts from a staged run.

The build half of promote, shared by two writers: ``scripts/bucket/promote_run.py``
(a Katana/offline run fetched from ``staging/``) and the native align pipeline
(``services/admin/align_pipeline/stage_assemble.py``, whose staged run is
materialised in-process). Nothing here writes to the bucket.

Every Inspector-side derivation the batch pipeline deliberately leaves undone
happens here, through Inspector's own writers rather than a second copy of them:
``services.segments.stamping`` for the persisted classifier fields,
``domain.identity.derive_uid`` for segment identity, ``services.audio.peaks`` +
``peaks_slim`` for chapter peaks, ``services.audio.op_peaks`` for the per-op
history-peaks records, and ``adapters.segments_json`` for ``segments.json``.
The staged shapes are read through ``qua_shared.schemas.bucket.staged_run``.

The one derivation that needs care is the waqf-sakt UID: it is derived once from
``final_index`` and reaches both the persisted row and the published operation,
never by assignment onto the read-only event model. See ``stamp_waqf_uids``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from qua_shared.schemas.bucket.staged_run import (
    ChapterCandidateDoc,
    PipelineAuditEvent,
    RunManifestDoc,
    stamp_operation,
)

log = logging.getLogger("inspector")

# Constant actor on every published pipeline batch — the offline writer's stamp.
PIPELINE_ACTOR = {"hf_user_id": "pipeline", "login_at_time": "pipeline", "role": "pipeline"}
_PIPELINE_ACTOR = PIPELINE_ACTOR

# Staged sidecars publish under their bare names at the reciter root.
SIDECAR_NAMES = (
    "low_confidence_v2.json",
    "auto_split_v1.json",
    "missed_waqf_v2.json",
    "verse_ends_v1.json",
)
_SIDECARS = SIDECAR_NAMES


def dumps(obj: Any) -> bytes:
    """Serialise a published JSON artifact the way the pipeline writer did."""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def jsonl_bytes(records: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")


_jsonl_bytes = jsonl_bytes


# ---------------------------------------------------------------------------
# The _meta block, built once for both artifacts
# ---------------------------------------------------------------------------


def _segmentation_knobs(profile: Any) -> dict[str, Any]:
    """The profile's segmentation params — where the ``_meta`` knobs live."""
    knobs = profile.get("segmentation") if isinstance(profile, dict) else None
    if not isinstance(knobs, dict):
        raise ValueError("manifest.profile carries no 'segmentation' params block")
    return knobs


def build_meta(manifest: RunManifestDoc) -> dict[str, Any]:
    """Project the run manifest onto the ``_meta`` block both artifacts carry.

    One construction, one dict, written verbatim into ``detailed.json`` and
    ``segments.json`` — exactly as the pipeline writer did. ``created_at`` is the
    run's own stamp, never promote's clock. The five segmentation knobs are read
    with ``.get()``: the boundary-head arm carries no ``min_silence_ms``, and
    ``exclude_none=True`` drops what it does not have. ``DetailedMeta`` is
    ``extra="forbid"``, so a mapping mistake raises here rather than publishing a
    silently empty provenance block.
    """
    from qua_shared.schemas.bucket.segment import DetailedMeta

    knobs = _segmentation_knobs(manifest.profile)
    return DetailedMeta(
        created_at=manifest.created_at,
        riwayah=meta_riwayah(manifest),
        asr_model=manifest.model_revisions.get("asr"),
        vad_model=manifest.model_revisions.get("vad"),
        min_silence_ms=knobs.get("min_silence_ms"),
        min_speech_ms=knobs.get("min_speech_ms"),
        pad_left_ms=knobs.get("pad_left_ms"),
        pad_right_ms=knobs.get("pad_right_ms"),
        min_silence_floor_ms=knobs.get("min_silence_floor_ms"),
        audio_source=manifest.inputs.audio_source,
    ).model_dump(exclude_none=True)


def meta_riwayah(manifest: RunManifestDoc) -> str | None:
    """The run's riwayah as an Inspector slug, or ``None`` when it is Hafs.

    ``None`` rather than ``"hafs_an_asim"`` on purpose: ``exclude_none=True``
    then drops the key, so a Hafs promote publishes byte-identical ``_meta`` to
    every run that came before multi-riwayah and no backfill is owed.
    """
    from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH, from_sdk_slug

    sdk_slug = manifest.inputs.riwayah
    if sdk_slug == DEFAULT_SDK_RIWAYAH:
        return None
    return from_sdk_slug(sdk_slug)


# ---------------------------------------------------------------------------
# Candidates, classifier stamps, waqf UIDs
# ---------------------------------------------------------------------------


def read_entries(run_dir: Path, manifest: RunManifestDoc) -> tuple[list[dict], dict[int, dict]]:
    """Validate every candidate and build the ``detailed.json`` entry list.

    Each candidate segment validates as a ``DetailedSegment`` unchanged — the
    producer already writes that model's field names and integer-millisecond
    units — so there is no rename step between the two validations. Returns the
    entries in chapter order plus a chapter index the waqf stamping joins on.
    """
    from qua_shared.schemas.bucket.segment import DetailedSegment

    by_chapter: dict[int, dict] = {}
    for chapter in sorted(manifest.inputs.chapters):
        path = run_dir / "candidates" / f"{chapter}.json"
        doc = ChapterCandidateDoc.model_validate(json.loads(path.read_bytes()))
        if doc.riwayah != manifest.inputs.riwayah:
            raise ValueError(
                f"candidates/{chapter}.json is {doc.riwayah!r} but the run is "
                f"{manifest.inputs.riwayah!r} — refs from two editions in one delivery"
            )
        for entry in doc.entries:
            key = int(str(entry.ref).split(":")[0])
            if key in by_chapter:
                raise ValueError(f"chapter {key} appears in more than one candidate file")
            by_chapter[key] = {
                "ref": entry.ref,
                "segments": [
                    DetailedSegment.model_validate(seg).model_dump(exclude_none=True)
                    for seg in entry.segments
                ],
            }
    return [by_chapter[ch] for ch in sorted(by_chapter)], by_chapter


def stamp_waqf_uids(
    by_chapter: dict[int, dict], events: list[PipelineAuditEvent]
) -> dict[int, str]:
    """Derive one UID per waqf-sakt event and write it to the persisted row.

    The merged row's position in the published list is ``final_index`` — the
    pre-strip ``index_at_save`` minus the specials removed before it — and the
    UID is derived from that index once, here. The same string reaches the
    published operation through ``stamp_operation``'s ``after_uid``; nothing is
    ever assigned onto the read-only event model, which declares no
    ``segment_uid`` and would raise.

    The ``time_start`` check is the exact cross-check: a wrong ``final_index``
    stops the promote instead of publishing an operation whose UID resolves to
    no row.
    """
    from domain.identity import derive_uid

    uids: dict[int, str] = {}
    for n, ev in enumerate(events):
        if ev.kind != "waqf_sakt":
            continue
        snap = ev.targets_after[0]
        index = snap.final_index
        if index is None:
            raise ValueError(f"waqf_sakt event {n} (chapter {ev.chapter}) carries no final_index")
        entry = by_chapter.get(ev.chapter)
        if entry is None:
            raise ValueError(f"waqf_sakt event {n}: no candidate entry for chapter {ev.chapter}")
        rows = entry["segments"]
        if not 0 <= index < len(rows):
            raise ValueError(
                f"waqf_sakt event {n}: final_index {index} outside chapter "
                f"{ev.chapter}'s {len(rows)} rows"
            )
        row = rows[index]
        if row["time_start"] != snap.time_start:
            raise ValueError(
                f"waqf_sakt event {n}: chapter {ev.chapter} row {index} starts at "
                f"{row['time_start']}ms, the snapshot at {snap.time_start}ms"
            )
        uid = derive_uid(ev.chapter, index, row["time_start"])
        row["segment_uid"] = uid
        uids[n] = uid
    return uids


def finalise_entries(entries: list[dict]) -> list[dict]:
    """Round-trip every seg through ``DetailedSegment`` for the on-disk shape."""
    from qua_shared.schemas.bucket.segment import DetailedSegment

    return [
        {
            "ref": e["ref"],
            "segments": [
                DetailedSegment.model_validate(s).model_dump(exclude_none=True)
                for s in e["segments"]
            ],
        }
        for e in entries
    ]


# ---------------------------------------------------------------------------
# The published edit history
# ---------------------------------------------------------------------------


def replay_events(
    events: list[PipelineAuditEvent],
    waqf_uids: dict[int, str],
    source_urls: dict[int, str],
    saved_at_utc: str,
) -> list[dict]:
    """Stamp every pipeline event into published ``edit_history.jsonl`` batches.

    Waqf-sakt merges go one batch per chapter; the specials strip goes in one
    ``strip_specials`` batch spanning every chapter it touched — the shape the
    pipeline writer produced. A delete's single ``targets_before`` snapshot gets
    a UID derived from its pre-strip index (that row does not exist post-strip
    and nothing joins to the value); a waqf's get none, as published.
    """
    from domain.identity import derive_uid
    from qua_shared.schemas.bucket.edit_history import EditHistoryBatch
    from utils.uuid7 import uuid7

    waqf_by_chapter: dict[int, list[dict]] = {}
    deletes: list[dict] = []
    delete_chapters: set[int] = set()

    for n, ev in enumerate(events):
        url = source_urls.get(ev.chapter)
        if not url:
            raise ValueError(f"event {n}: chapter {ev.chapter} has no chapter_sources.json url")
        before_uids: list[str | None] | None = (
            [derive_uid(ev.chapter, s.index_at_save, s.time_start) for s in ev.targets_before]
            if ev.kind == "delete_segment"
            else None
        )
        op = stamp_operation(
            ev, op_id=uuid7(), audio_url=url, after_uid=waqf_uids.get(n), before_uids=before_uids
        )
        if ev.kind == "waqf_sakt":
            waqf_by_chapter.setdefault(ev.chapter, []).append(op)
        else:
            deletes.append(op)
            delete_chapters.add(ev.chapter)

    def _batch(**fields) -> dict:
        base = {"schema_version": 1, "batch_id": uuid7(), "saved_at_utc": saved_at_utc}
        return EditHistoryBatch.model_validate(
            {**base, "actor": _PIPELINE_ACTOR, **fields}
        ).model_dump(exclude_none=True)

    batches = [_batch(chapter=ch, operations=waqf_by_chapter[ch]) for ch in sorted(waqf_by_chapter)]
    if deletes:
        batches.append(
            _batch(
                batch_type="strip_specials",
                chapter=None,
                chapters=sorted(delete_chapters),
                operations=deletes,
            )
        )
    return batches


# ---------------------------------------------------------------------------
# Chapter peaks and per-op history peaks
# ---------------------------------------------------------------------------


def build_chapter_peaks(
    run_dir: Path,
    source_urls: dict[int, str],
    *,
    peaks_blobs: dict[int, bytes] | None = None,
) -> tuple[list[int], dict[str, dict]]:
    """Bake ``peaks/<ch>.json.gz`` beside the staged audio; return the envelopes.

    ``peaks_blobs`` (``{chapter: slim gzip bytes}``) short-circuits the ffmpeg
    bake: the native pipeline's acquire job already wrote the blobs to the
    bucket, so the envelopes are unpacked from those instead of re-decoding
    audio in the Space process. Nothing is written under ``run_dir`` then.

    The envelopes are keyed by the **raw** ``chapter_sources.json`` url — the
    identical string stamped as the ops' ``audio_url``, so ``build_op_records``
    finds them. Normalisation happens only in the emitted record's ``url``,
    where it always did.

    A url claimed by more than one chapter (a combined source file) is ambiguous
    under a url-keyed provider, so it gets no envelope and its ops fall back to
    lazy-on-play rather than being sliced from a sibling chapter's waveform.
    Reported, never silent.
    """
    from services.audio.peaks_slim import unpack_slim_envelope

    chapters: list[int] = []
    envelopes: dict[str, dict] = {}
    shared: set[str] = set()
    for chapter, blob in _chapter_blobs(run_dir, peaks_blobs):
        chapters.append(chapter)

        url = source_urls.get(chapter)
        if not url:
            continue
        if url in envelopes or url in shared:
            envelopes.pop(url, None)
            shared.add(url)
            continue
        env = unpack_slim_envelope(blob)
        if env is not None:
            envelopes[url] = env
    for url in sorted(shared):
        log.info(
            "promote_build: %s covers several chapters — per-op peaks left to lazy-on-play", url
        )
    return chapters, envelopes


def _chapter_blobs(run_dir: Path, peaks_blobs: dict[int, bytes] | None):
    """``(chapter, slim blob)`` in chapter order — given, or baked from ``audio/``."""
    from services.audio.peaks import compute_audio_peaks
    from services.audio.peaks_slim import pack_slim

    if peaks_blobs is not None:
        yield from sorted(peaks_blobs.items())
        return
    peaks_dir = run_dir / "peaks"
    peaks_dir.mkdir(parents=True, exist_ok=True)
    for mp3 in sorted((run_dir / "audio").glob("*.mp3"), key=lambda p: int(p.stem)):
        chapter = int(mp3.stem)
        hd = compute_audio_peaks(str(mp3))
        if hd is None:
            raise ValueError(f"chapter {chapter}: ffmpeg produced no peaks for {mp3}")
        blob = pack_slim(hd)
        (peaks_dir / f"{chapter}.json.gz").write_bytes(blob)
        yield chapter, blob


def build_peaks_records(
    slug: str, batches: list[dict], source_urls: dict[int, str], envelopes: dict[str, dict]
) -> list[dict]:
    """Slice the in-memory envelopes into per-op history-peaks records.

    Reuses Inspector's own record builder through its envelope provider. The
    default provider reads baked peaks off the bucket, where this run's do not
    exist yet — every op would take the skip branch and the promote would
    succeed with an empty ``edit_history_peaks.jsonl``.
    """
    from services.audio import op_peaks

    ops = [op for batch in batches for op in batch.get("operations", [])]
    if not ops:
        return []
    return op_peaks.build_op_records(slug, ops, dict(source_urls), env_provider=envelopes.get)


# ---------------------------------------------------------------------------
# The whole build
# ---------------------------------------------------------------------------


def build_artifacts(
    run_dir: Path,
    manifest: RunManifestDoc,
    slug: str,
    *,
    peaks_blobs: dict[int, bytes] | None = None,
) -> dict[str, bytes]:
    """Every JSON/JSONL artifact promote publishes, keyed by reciter-relative name.

    ``peaks_blobs`` is forwarded to ``build_chapter_peaks`` (see there).
    """
    from adapters.segments_json import build_segments_doc
    from config import DK_SCRIPT_PATH, SURAH_INFO_PATH
    from qua_shared.schemas.bucket.pipeline_meta import PipelineMeta
    from services.segments import stamping

    for path in (SURAH_INFO_PATH, DK_SCRIPT_PATH):
        if not Path(path).is_file():
            raise ValueError(f"reference data missing: {path} — every stamp would be empty")

    entries, by_chapter = read_entries(run_dir, manifest)
    n_segs = sum(len(e["segments"]) for e in entries)
    # The classifier stamps read word counts and script from the delivery's own
    # edition; stamping a Warsh delivery against Hafs counts marks real segments
    # as over-length and misses its qalqala letters.
    n_stamped = stamping.stamp_entries(entries, riwayah=manifest.inputs.riwayah)
    if n_stamped != n_segs:
        raise ValueError(f"stamped {n_stamped} of {n_segs} segments")

    events = [
        PipelineAuditEvent.model_validate(e)
        for e in json.loads((run_dir / "events.json").read_bytes())
    ]
    waqf_uids = stamp_waqf_uids(by_chapter, events)
    disk_entries = finalise_entries(entries)

    meta = build_meta(manifest)
    source_urls = {
        int(ch): rec["url"]
        for ch, rec in json.loads((run_dir / "chapter_sources.json").read_bytes()).items()
    }
    batches = replay_events(events, waqf_uids, source_urls, manifest.created_at)

    built: dict[str, bytes] = {
        "detailed.json": dumps({"_meta": meta, "entries": disk_entries}),
        "segments.json": dumps(build_segments_doc(disk_entries, meta, with_repeated=True)),
        "pipeline_meta.json": dumps(
            PipelineMeta(
                schema_version=1,
                # The run's stamp, not promote's clock — one run, one timestamp.
                generated_at=manifest.created_at,
                deleted_basmala_chapters=sorted(manifest.deleted_basmala_chapters),
                riwayah=meta_riwayah(manifest),
                # exclude_none so a Hafs run's sidecar keeps the bytes it has always
                # had — the key appears only where it says something.
            ).model_dump(mode="json", exclude_none=True)
        ),
        "chapter_sources.json": (run_dir / "chapter_sources.json").read_bytes(),
    }
    coverage = run_dir / "coverage_report.json"
    if coverage.is_file():
        built["coverage_report.json"] = coverage.read_bytes()
    for name in _SIDECARS:
        sidecar = run_dir / "sidecars" / name
        if sidecar.is_file():
            built[name] = sidecar.read_bytes()
    if batches:
        built["edit_history.jsonl"] = _jsonl_bytes(batches)

    chapters, envelopes = build_chapter_peaks(run_dir, source_urls, peaks_blobs=peaks_blobs)
    records = build_peaks_records(slug, batches, source_urls, envelopes)
    if records:
        built["edit_history_peaks.jsonl"] = _jsonl_bytes(records)
    log.info(
        "promote_build: built %d chapter(s), %d segment(s), %d batch(es), %d peaks record(s)",
        len(chapters),
        n_segs,
        len(batches),
        len(records),
    )
    return built
