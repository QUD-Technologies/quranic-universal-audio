"""Stage 2b — turn aligned combined files into per-chapter chapters (and guard
single-chapter files against a mislabelled plan).

Runs after ``align`` has staged every group. For each combined source it
partitions the staged rows by surah (``partition.py``), writes the cut windows
to ``split_plan.json`` and launches ``qua_jobs/split_audio.py`` (kind
``split_audio``) to cut ``audio/<slot>.mp3`` into ``audio/<ch>.mp3`` + peaks.
Once the cuts land, each chapter's rows are rebased onto its cut and staged as
``chapters/<ch>.json`` — from here on a combined chapter is indistinguishable
from any other, so sidecars and assemble need no special case.

Coverage is tolerant, not fatal: a planned chapter a file does not hold is
dropped from the delivery, an unplanned surah it does hold is adopted, and a
single-chapter file whose audio is another surah is dropped. Every such call is
recorded in ``split_outcome.json`` and published in ``coverage_report.json``.
"""

from __future__ import annotations

import logging

from services.storage import storage_paths
from services.storage.hf_bucket import get_backend

from . import manifest, partition, progress, stage_acquire, staging
from .params import AUTO_SPLIT_TIMING_SOURCE
from .sources import SourceGroup

log = logging.getLogger("inspector")

KIND = "split_audio"
_ENTRYPOINT = "python /aux/code/qua_jobs/split_audio.py"


def run(slug: str, run_id: str, groups: list[SourceGroup]) -> dict:
    """Returns the outcome ``{dropped, adopted, mismatched}``."""
    done = staging.read_json(staging.run_file(slug, run_id, staging.SPLIT_OUTCOME_FILE))
    if done is not None:
        log.info("align %s: split already applied, skipped", run_id)
        return done
    outcome = {"dropped": [], "adopted": {}, "mismatched": {}, "ignored": {}}
    _guard_singles(slug, run_id, groups, outcome)
    combined = [g for g in groups if g.combined]
    if combined:
        _split_combined(slug, run_id, groups, combined, outcome)
    kept = {int(c) for c in outcome["adopted"]}
    _delete_dropped_audio(slug, [c for c in outcome["dropped"] if c not in kept])
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
        manifest.apply_split(slug, cuts={}, dropped=outcome["dropped"], adopted={})


# ---------------------------------------------------------------------------
# Combined files
# ---------------------------------------------------------------------------


def _split_combined(
    slug: str,
    run_id: str,
    groups: list[SourceGroup],
    combined: list[SourceGroup],
    outcome: dict,
) -> None:
    acquired = staging.read_json(staging.acquire_path(slug, run_id)) or {}
    durations = {int(k): v.get("duration_ms") for k, v in (acquired.get("sources") or {}).items()}
    singles = {g.chapters[0] for g in groups if not g.combined} - set(outcome["dropped"])
    plan: dict[str, dict] = {}
    staged_rows: dict[int, tuple[dict, list[dict], int]] = {}
    for g in combined:
        doc = staging.read_json(staging.source_path(slug, run_id, g.slot))
        if doc is None:
            raise FileNotFoundError(f"staged source {g.slot} missing for {slug}/{run_id}")
        others = singles | {c for o in combined if o is not g for c in o.chapters}
        part = partition.partition(
            doc.get("segments") or [],
            planned=g.chapters,
            claimed_elsewhere=others,
            duration_ms=durations.get(g.slot),
        )
        for ch in part.missing:
            outcome["dropped"].append(ch)
        for ch in part.cuts:
            if ch not in g.chapters:
                outcome["adopted"][str(ch)] = g.url
        if part.ignored:
            outcome["ignored"][str(g.slot)] = part.ignored
        plan[str(g.slot)] = {
            "chapters": {str(c): [cut.start_ms, cut.end_ms] for c, cut in part.cuts.items()}
        }
        for c, cut in part.cuts.items():
            staged_rows[c] = (doc, cut.rows, cut.start_ms)
    split_report = _cut(slug, run_id, plan)
    for c, (doc, rows, offset) in staged_rows.items():
        staged = {k: v for k, v in doc.items() if k != "segments"}
        staged["segments"] = partition.rebase(rows, offset)
        staged["_inspector"] = {
            **(doc.get("_inspector") or {}),
            "auto_split_timing_source": AUTO_SPLIT_TIMING_SOURCE,
            "split_offset_ms": offset,
        }
        staging.write_json(staging.chapter_path(slug, run_id, c), staged)
    cuts = {int(k): v for k, v in (split_report.get("cuts") or {}).items()}
    adopted = {int(k): v for k, v in outcome["adopted"].items()}
    manifest.apply_split(slug, cuts=cuts, dropped=outcome["dropped"], adopted=adopted)


def _cut(slug: str, run_id: str, plan: dict) -> dict:
    """Launch (or resume) the split job and wait for its report."""
    report_path = staging.run_file(slug, run_id, staging.SPLIT_REPORT_FILE)
    job_file = staging.run_file(slug, run_id, staging.SPLIT_JOB_FILE)
    job = staging.read_json(job_file) or {}
    job_id = job.get("job_id")
    if job.get("failed") or not job_id:
        staging.write_json(staging.run_file(slug, run_id, staging.SPLIT_PLAN_FILE), {"slots": plan})
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
