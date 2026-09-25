"""Stage 4 — build and publish ``reciters/<slug>/`` from the staged run.

Materialises a promote-shaped run directory in a temp dir (candidates, events,
chapter sources, sidecars, coverage), synthesises the run manifest the shared
``promote_build`` reads, and writes every artifact to the bucket — peaks come
from the blobs acquire already baked, so no audio is decoded in-process.
``auto_detect`` then sees ``detailed.json`` and fires ``alignment_completed``.

Guarded: the slug must still be awaiting alignment (or merely catalogued) and
have no ``detailed.json`` — a delivery that got content some other way is never
overwritten.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from qua_shared.schemas import ReciterState
from qua_shared.schemas.bucket.staged_run import RunInputsDoc, RunManifestDoc
from services.state import state as state_service
from services.storage import cache, storage_paths
from services.storage.hf_bucket import StorageNotFound, get_backend

from . import adapt, staging
from . import params as _params
from .params import AlignParams
from .stage_sidecars import AUTO_SPLIT_FILE, LOW_CONFIDENCE_FILE

log = logging.getLogger("inspector")

_ASSEMBLABLE_STATES = (ReciterState.CATALOGUED, ReciterState.AWAITING_ALIGNMENT)
_SOURCE_COMMIT = "inspector-native"


class AssembleError(RuntimeError):
    pass


def guard(slug: str) -> None:
    row = state_service.get_row(slug)
    if row is None:
        raise AssembleError(f"{slug}: no state row")
    if row.state not in _ASSEMBLABLE_STATES:
        raise AssembleError(f"{slug}: state is {row.state.value}, refusing to overwrite content")
    if get_backend().exists(storage_paths.detailed_path(slug)):
        raise AssembleError(f"{slug}: detailed.json already exists, refusing to overwrite")


def run(
    slug: str,
    run_id: str,
    params: AlignParams,
    chapters: list[int],
    sources: dict[int, str],
    *,
    started_at: str,
) -> dict[str, int]:
    from services.segments import promote_build

    guard(slug)
    docs = staging.read_chapters(slug, run_id, chapters)
    with tempfile.TemporaryDirectory(prefix=f"align_{slug}_") as tmp:
        run_dir = Path(tmp)
        deleted_basmala = _materialise(run_dir, docs, chapters, sources, params.riwayah)
        _materialise_sidecars(run_dir, slug, run_id, params.riwayah)
        outcome = staging.read_json(staging.run_file(slug, run_id, staging.SPLIT_OUTCOME_FILE))
        _write_coverage(run_dir, chapters, outcome or {})
        manifest = _manifest(slug, run_id, params, chapters, deleted_basmala, started_at)
        built = promote_build.build_artifacts(
            run_dir, manifest, slug, peaks_blobs=_peaks_blobs(slug, chapters)
        )

    backend = get_backend()
    # detailed.json last: it is the sentinel auto_detect + every reader gate on.
    for name in sorted(built, key=lambda n: n == "detailed.json"):
        backend.write_bytes_atomic(storage_paths.reciter_file(slug, name), built[name])
    cache.invalidate_seg_caches(slug)
    if not _params.keep_staging():
        staging.delete_run(slug, run_id)
    log.info("align %s: assembled %d artifact(s) for %s", run_id, len(built), slug)
    return {"artifacts": len(built), "chapters": len(chapters)}


def _materialise(run_dir, docs, chapters, sources, riwayah) -> list[int]:
    (run_dir / "candidates").mkdir(parents=True)
    events: list[dict] = []
    deleted_basmala: list[int] = []
    for ch in chapters:
        candidate, ch_events, basmala = adapt.adapt_chapter(
            ch, docs[ch], source_url=sources[ch], riwayah=riwayah
        )
        _dump(run_dir / "candidates" / f"{ch}.json", candidate)
        events.extend(ch_events)
        if basmala:
            deleted_basmala.append(ch)
    _dump(run_dir / "events.json", events)
    _dump(
        run_dir / "chapter_sources.json",
        {
            str(ch): {"url": sources[ch], "offset_ms": adapt.source_offset(docs[ch])}
            for ch in chapters
        },
    )
    return deleted_basmala


def _materialise_sidecars(run_dir: Path, slug: str, run_id: str, riwayah: str | None) -> None:
    """Copy the staged sidecars into the run dir ``promote_build`` reads.

    ``auto_split_v1`` is always owed. ``low_confidence_v2`` is owed only on Hafs:
    a non-Hafs delivery never gets the probe (D12 — its question is Hafs-only),
    so its absence there is the contract, not a stage that failed to stage.
    """
    (run_dir / "sidecars").mkdir()
    required = [AUTO_SPLIT_FILE]
    if (riwayah or DEFAULT_SDK_RIWAYAH) == DEFAULT_SDK_RIWAYAH:
        required.append(LOW_CONFIDENCE_FILE)
    for name in (LOW_CONFIDENCE_FILE, AUTO_SPLIT_FILE):
        doc = staging.read_json(staging.sidecar_path(slug, run_id, name))
        if doc is None:
            if name in required:
                raise AssembleError(f"staged sidecar {name} missing for {slug}/{run_id}")
            continue
        _dump(run_dir / "sidecars" / name, doc)


def _write_coverage(run_dir: Path, chapters: list[int], outcome: dict) -> None:
    """``outcome`` is the split stage's record: chapters dropped because their
    file did not hold them (or held another surah) are ``missing``; the
    mislabelled single files are listed as ``unresolved_files``."""
    missing = sorted(set(outcome.get("dropped") or []))
    unresolved = [
        f"chapter {ch}: audio is surah {surah}"
        for ch, surah in sorted(
            (outcome.get("mismatched") or {}).items(), key=lambda kv: int(kv[0])
        )
    ]
    _dump(
        run_dir / "coverage_report.json",
        {
            "created_at": _now(),
            "clean": bool(chapters) and not missing,
            "discovered_count": len(chapters),
            "discovered": list(chapters),
            "missing": missing,
            "duplicates": {},
            "anchor_failures": [],
            "unresolved_files": unresolved,
        },
    )


def _manifest(slug, run_id, params: AlignParams, chapters, deleted_basmala, started_at):
    knobs = {
        "pad_left_ms": params.pad_left_ms,
        "pad_right_ms": params.pad_right_ms,
        "min_silence_floor_ms": params.min_silence_floor_ms,
    }
    return RunManifestDoc(
        run_id=run_id,
        slug=slug,
        source_commit=_SOURCE_COMMIT,
        created_at=started_at,
        profile={"name": "inspector-native", "segmentation": knobs},
        profile_sha256="",
        model_revisions={
            "asr": _params.ASR_MODEL_IDS.get(params.model_name, params.model_name),
            "vad": _params.VAD_MODEL_ID,
        },
        inputs=RunInputsDoc(
            slug=slug,
            chapters=list(chapters),
            audio_source=None,
            riwayah=params.riwayah or DEFAULT_SDK_RIWAYAH,
        ),
        artifacts=[],
        required=[],
        deleted_basmala_chapters=sorted(deleted_basmala),
    )


def _peaks_blobs(slug: str, chapters: list[int]) -> dict[int, bytes]:
    backend = get_backend()
    blobs: dict[int, bytes] = {}
    for ch in chapters:
        try:
            blobs[ch] = backend.read_bytes(storage_paths.reciter_file(slug, f"peaks/{ch}.json.gz"))
        except StorageNotFound as exc:
            raise AssembleError(f"chapter {ch}: peaks blob missing on the bucket") from exc
    return blobs


def _dump(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
