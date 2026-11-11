"""Mint a reviewed intake plan into the catalog and start its align run.

The plan's identity + included files become the ``intake.ingest`` body
(delivery, reciter, vocab additions, audio manifest), the mint flips the request
to ``accepted`` and seeds the new slug's pending request, and the align pipeline
starts on it.

* Playlist files go into the manifest's ``sources`` with no chapters: the run
  aligns each one, the aligner detects the surahs it holds, and the split writes
  the chapters.
* ``links`` keep the contributor's chapters. Links sharing one URL (a combined
  file) get a unique bucket ``url`` per chapter and keep the original file as
  ``source_url`` — the pipeline acquires that file once and splits it.

The mint always lands before the run starts. A start the pipeline refuses (the
shared budget, missing config) is reported in the response; the Align button on
the new slug row retries it.
"""

from __future__ import annotations

import logging

from qua_shared.schemas import Actor, IntakeAlignResponse, IntakePlan
from services.admin import intake as intake_service
from services.admin.align_pipeline import runs as align_runs
from services.admin.align_pipeline.sources import bucket_chapter_url
from services.db import _serde, repo_catalog

from . import plan as _plan

log = logging.getLogger("inspector")

_BY_SURAH = "by_surah"
_REASON = "online intake: planned + aligned from the Requests tab"


def mint_and_align(
    request_id: str, actor: Actor, *, device: str, exempt: bool
) -> IntakeAlignResponse:
    row = _plan.pending_intake(request_id)
    stored = _plan.stored_plan(row)
    if stored is None or stored.status != "ready":
        raise _plan.PlanError("build and review the plan first", 409)
    view = _plan.to_view(stored, kind=row["kind"])
    if view.errors:
        raise _plan.PlanError("; ".join(view.errors), 400)
    payload = _serde.json_loads(row["payload"]) or {}
    body = ingest_body(stored, kind=row["kind"], payload=payload)
    minted = intake_service.ingest(request_id, body, actor=actor)
    slug = minted["slug"]
    log.info("intake %s: minted %s from its online plan", request_id, slug)
    try:
        align_runs.start(slug, actor, device=device, exempt=exempt)
    except align_runs.AlignRunError as exc:
        log.warning("intake %s: %s minted but align did not start: %s", request_id, slug, exc)
        return IntakeAlignResponse(slug=slug, state=minted.get("state"), align_error=str(exc))
    return IntakeAlignResponse(slug=slug, state=minted.get("state"), align_started=True)


def ingest_body(plan: IntakePlan, *, kind: str, payload: dict) -> dict:
    ident = plan.identity
    edits = payload.get("proposed_edits") or {}
    body: dict = {
        "reason": _REASON,
        "delivery": {
            "slug": ident.slug,
            "reciter_id": ident.reciter_id,
            "riwayah": edits.get("riwayah"),
            "style": edits.get("style"),
            "source": ident.source,
            "channel": ident.channel,
            "source_url": plan.source_url if plan.host != "links" else None,
            "audio_category": _BY_SURAH,
            "recording_context": edits.get("recording_context"),
            "recording_year": ident.recording_year,
        },
        "audio_manifest": manifest_body(plan, ident.slug),
    }
    if kind == "new_reciter" and repo_catalog.find_reciter(ident.reciter_id) is None:
        body["reciter"] = {
            "reciter_id": ident.reciter_id,
            "name_en": ident.name_en or ident.reciter_id,
            "name_ar": ident.name_ar,
            "country": ident.country,
        }
    if repo_catalog.find_source(ident.source) is None:
        body["vocab_additions"] = {
            "sources": [
                {
                    "slug": ident.source,
                    "name": ident.new_source_name or ident.source,
                    "url": ident.new_source_url,
                    "audio_categories": [_BY_SURAH],
                }
            ]
        }
    return body


def manifest_body(plan: IntakePlan, slug: str) -> dict:
    included = [e for e in plan.entries if e.include]
    if plan.host != "links":
        return {"chapters": {}, "sources": [{"url": e.url, "title": e.title} for e in included]}
    return {"chapters": _link_chapters(included, slug)}


def _link_chapters(entries: list, slug: str) -> dict[str, dict]:
    chapters: dict[str, dict] = {}
    for entry in entries:
        if len(entry.chapters) == 1:
            chapters[str(entry.chapters[0])] = {"url": entry.url}
            continue
        for ch in entry.chapters:
            chapters[str(ch)] = {"url": bucket_chapter_url(slug, ch), "source_url": entry.url}
    return dict(sorted(chapters.items(), key=lambda kv: int(kv[0])))
