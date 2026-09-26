"""Stage 2b — turn aligned slot files into per-chapter chapters (and guard
single-chapter files against a mislabelled plan).

Runs after ``align`` has staged every group. Every slot file — a combined file
the manifest labelled, or a playlist file whose surahs only the aligner knows —
is cut by surah (``partition.cut_file``); ``resolve.py`` decides which file each
surah is taken from (stitching a surah uploaded in parts). The windows go to
``split_plan.json`` and ``qua_jobs/split_audio.py`` (kind ``split_audio``) cuts
``audio/<slot>.mp3`` into ``audio/<ch>.mp3`` + peaks. Once the cuts land, each
chapter's rows are rebased onto its cut and staged as ``chapters/<ch>.json`` —
from here on a cut chapter is indistinguishable from any other, so sidecars and
assemble need no special case.

Coverage is tolerant, not fatal: a planned chapter nobody holds is dropped, a
surah nobody planned is adopted, a single-chapter file whose audio is another
surah is dropped, a file with no recitation is reported, a surah missing inside
the delivery's span is a gap, and a file repeating a surah taken from another
file is reported. Every call is recorded
in ``split_outcome.json`` and published in ``coverage_report.json``.
"""

from __future__ import annotations

import logging

from services.storage import storage_paths
from services.storage.hf_bucket import get_backend

from . import manifest, partition, progress, resolve, stage_acquire, staging
from .params import AUTO_SPLIT_TIMING_SOURCE
from .sources import SourceGroup

log = logging.getLogger("inspector")

KIND = "split_audio"
_ENTRYPOINT = "python /aux/code/qua_jobs/split_audio.py"


def run(slug: str, run_id: str, groups: list[SourceGroup]) -> dict:
    """Returns the outcome recorded in ``split_outcome.json``."""
    done = staging.read_json(staging.run_file(slug, run_id, staging.SPLIT_OUTCOME_FILE))
    if done is not None:
        log.info("align %s: split already applied, skipped", run_id)
        return done
    outcome: dict = {
        "dropped": [],
        "adopted": {},
        "mismatched": {},
        "ignored": {},
        "suspect": {},
        "empty_sources": [],
        "fragments": {},
        "repeats": {},
        "gaps": [],
        "stitched": {},
    }
    _guard_singles(slug, run_id, groups, outcome)
    slotted = [g for g in groups if g.combined]
    if slotted:
        _split_slots(slug, run_id, groups, slotted, outcome)
    staging.write_json(staging.run_file(slug, run_id, staging.SPLIT_OUTCOME_FILE), outcome)
    return outcome


# ---------------------------------------------------------------------------
# Single-chapter files
# ---------------------------------------------------------------------------


def _guard_singles(slug: str, run_id: str, groups: list[SourceGroup], outcome: dict) -> None:
    for g in groups:
        if g.combined:
            continue
        ch = g.chapters[0]
        doc = staging.read_json(staging.chapter_path(slug, run_id, ch)) or {}
        other = partition.dominant_other_surah(ch, doc.get("segments") or [])
        if other is None:
            continue
        log.warning("align %s: chapter %d's audio is surah %d — dropped", run_id, ch, other)
        outcome["mismatched"][str(ch)] = other
        outcome["dropped"].append(ch)
    if outcome["dropped"]:
        # Before any cut: another file may provide the surah, and the split job
        # keeps an already-persisted chapter mp3 instead of re-cutting it.
        _delete_dropped_audio(slug, outcome["dropped"])
        manifest.apply_split(slug, cuts={}, dropped=outcome["dropped"])


# ---------------------------------------------------------------------------
# Slot files (combined + detect)
# ---------------------------------------------------------------------------


def _split_slots(
    slug: str,
    run_id: str,
    groups: list[SourceGroup],
    slotted: list[SourceGroup],
    outcome: dict,
) -> None:
    acquired = staging.read_json(staging.acquire_path(slug, run_id)) or {}
    durations = {int(k): v.get("duration_ms") for k, v in (acquired.get("sources") or {}).items()}
    fixed = {g.chapters[0] for g in groups if not g.combined} - set(outcome["dropped"])
    files: list[resolve.FileCuts] = []
    docs: dict[int, dict] = {}
    for g in slotted:
        doc = staging.read_json(staging.source_path(slug, run_id, g.item))
        if doc is None:
            raise FileNotFoundError(f"staged source {g.item} missing for {slug}/{run_id}")
        docs[g.item] = doc
        cuts = partition.cut_file(doc.get("segments") or [], durations.get(g.item))
        files.append(resolve.FileCuts(item=g.item, url=g.url, planned=g.chapters, cuts=cuts))
    res = resolve.resolve(files, fixed, _ayah_counts())
    _record(res, outcome)
    plan = {
        "chapters": {
            str(ch): [[p.file.item, p.cut.start_ms, p.cut.end_ms] for p in pieces]
            for ch, pieces in res.chapters.items()
        },
        "slots": [g.item for g in slotted],
    }
    report = _cut(slug, run_id, plan)
    for ch, pieces in res.chapters.items():
        _stage_chapter(slug, run_id, ch, pieces, docs)
    cuts = {}
    for key, cut in (report.get("cuts") or {}).items():
        first = res.chapters[int(key)][0]
        cuts[int(key)] = {**cut, "source_url": first.file.url, "offset_ms": first.cut.start_ms}
    manifest.apply_split(slug, cuts=cuts, dropped=outcome["dropped"], clear_sources=True)


def _ayah_counts() -> dict[int, int]:
    from services.storage.data_loader import load_surah_info_lite

    return {int(n): int(info["num_verses"]) for n, info in load_surah_info_lite().items()}


def _record(res: resolve.Resolution, outcome: dict) -> None:
    outcome["dropped"] = sorted((set(outcome["dropped"]) | set(res.dropped)) - set(res.chapters))
    outcome["adopted"] = {str(ch): res.chapters[ch][0].file.url for ch in res.adopted}
    outcome["ignored"] = {str(item): chs for item, chs in res.ignored.items()}
    outcome["suspect"] = {str(ch): note for ch, note in res.suspect.items()}
    outcome["empty_sources"] = list(res.empty)
    outcome["fragments"] = {str(ch): note for ch, note in res.fragments.items()}
    outcome["repeats"] = {
        url: {str(ch): other for ch, other in taken.items()} for url, taken in res.repeats.items()
    }
    outcome["gaps"] = list(res.gaps)
    outcome["stitched"] = {
        str(ch): len(pieces) for ch, pieces in res.chapters.items() if len(pieces) > 1
    }
    for url in res.empty:
        log.warning("split: no recitation detected in %s", url)


def _stage_chapter(
    slug: str, run_id: str, ch: int, pieces: list[resolve.Piece], docs: dict[int, dict]
) -> None:
    """The chapter's rows on its cut timeline — pieces laid end to end."""
    rows: list[dict] = []
    shift = 0
    for p in pieces:
        rebased = partition.rebase(p.cut.rows, p.cut.start_ms - shift)
        rows.extend(rebased)
        shift += p.cut.end_ms - p.cut.start_ms
    first = pieces[0]
    doc = docs[first.file.item]
    staged = {k: v for k, v in doc.items() if k != "segments"}
    staged["segments"] = rows
    staged["_inspector"] = {
        **(doc.get("_inspector") or {}),
        "auto_split_timing_source": AUTO_SPLIT_TIMING_SOURCE,
        "split_offset_ms": first.cut.start_ms,
    }
    if len(pieces) > 1:
        staged["_inspector"]["split_pieces"] = [
            {"url": p.file.url, "start_ms": p.cut.start_ms, "end_ms": p.cut.end_ms} for p in pieces
        ]
    staging.write_json(staging.chapter_path(slug, run_id, ch), staged)


def _cut(slug: str, run_id: str, plan: dict) -> dict:
    """Launch (or resume) the split job and wait for its report."""
    report_path = staging.run_file(slug, run_id, staging.SPLIT_REPORT_FILE)
    job_file = staging.run_file(slug, run_id, staging.SPLIT_JOB_FILE)
    job = staging.read_json(job_file) or {}
    job_id = job.get("job_id")
    if job.get("failed") or not job_id:
        staging.write_json(staging.run_file(slug, run_id, staging.SPLIT_PLAN_FILE), plan)
        job_id = stage_acquire.launch_job(KIND, _ENTRYPOINT, slug, run_id, deps="numpy")
        staging.write_json(job_file, {"job_id": job_id})
    progress.set_detail(run_id, aligner_stage="splitting")
    try:
        return stage_acquire.wait_job(KIND, slug, run_id, job_id, report_path)
    except stage_acquire.AcquireError:
        staging.write_json(job_file, {"job_id": job_id, "failed": True})
        raise


def _delete_dropped_audio(slug: str, dropped: list[int]) -> None:
    backend = get_backend()
    for ch in dropped:
        for name in (f"audio/{ch}.mp3", f"peaks/{ch}.json.gz"):
            try:
                backend.delete(storage_paths.reciter_file(slug, name))
            except Exception:  # noqa: BLE001 — absent is fine
                pass
