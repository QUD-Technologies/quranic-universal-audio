"""The intake plan lifecycle: enumerate → match → owner review → (mint.py) align.

``payload.plan`` on the request row holds the plan. Enumeration can take a
minute (a SoundCloud set resolves every track), so ``build`` stamps
``status="enumerating"`` and a daemon thread fills the plan in; the Requests tab
polls. A thread killed by a restart leaves a stale ``enumerating`` plan, which
reads as failed after ``STALE_ENUMERATION_S`` so the owner can rebuild it.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import cast

from qua_shared.schemas import (
    IntakePlan,
    IntakePlanUpdate,
    IntakePlanView,
    IntakeSource,
    PlanCoverage,
    PlanEntry,
    PlanOption,
)
from qua_shared.schemas.wire.intake_plan import MatchConfidence, PlanHost
from services.db import _serde, repo_catalog, repo_requests
from services.db import sync as _sync

from . import enumerate as _enumerate
from . import identity as _identity
from . import match as _match

log = logging.getLogger("inspector")

INTAKE_KINDS = ("existing_reciter_new_combo", "new_reciter")
STALE_ENUMERATION_S = 15 * 60
LAST_CHAPTER = 114
#: Longer single files (a whole-mushaf video) may exceed what the aligner can
#: hold in one request; flagged, not refused.
LONG_FILE_S = 6 * 3600


class PlanError(Exception):
    """Refused plan operation. ``status`` is the HTTP code the route maps to."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Row access
# ---------------------------------------------------------------------------


def pending_intake(request_id: str):
    row = repo_requests.get_by_id(request_id)
    if row is None or row["kind"] not in INTAKE_KINDS:
        raise PlanError("unknown intake request", 404)
    if row["status"] != "pending" or row["slug"]:
        raise PlanError(f"request is {row['status']}, not a pending intake", 409)
    return row


def stored_plan(row) -> IntakePlan | None:
    raw = (_serde.json_loads(row["payload"]) or {}).get("plan")
    if not raw:
        return None
    plan = IntakePlan.model_validate(raw)
    if plan.status == "enumerating" and _age_s(plan.updated_at) > STALE_ENUMERATION_S:
        plan.status, plan.error = "failed", "enumeration was interrupted — rebuild the plan"
    return plan


def _save(request_id: str, plan: IntakePlan, *, expect_created_at: str | None = None) -> bool:
    """Write ``plan`` onto the row. With ``expect_created_at``, only if the stored
    plan is still that build (a rebuild in between wins)."""
    with _sync.durable_transaction():
        row = repo_requests.get_by_id(request_id)
        if row is None:
            return False
        payload = _serde.json_loads(row["payload"]) or {}
        current = payload.get("plan") or {}
        if expect_created_at is not None and current.get("created_at") != expect_created_at:
            return False
        payload["plan"] = plan.model_dump(mode="json")
        repo_requests.set_payload(request_id, payload)
    from services.storage import cache

    cache.invalidate_admin_requests_cache()
    return True


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def get(request_id: str) -> IntakePlanView | None:
    row = repo_requests.get_by_id(request_id)
    if row is None or row["kind"] not in INTAKE_KINDS:
        raise PlanError("unknown intake request", 404)
    plan = stored_plan(row)
    return to_view(plan, kind=row["kind"]) if plan else None


def build(request_id: str) -> IntakePlanView:
    """(Re)enumerate the source in the background; returns the pending plan."""
    row = pending_intake(request_id)
    current = stored_plan(row)
    if current is not None and current.status == "enumerating":
        return to_view(current, kind=row["kind"])
    now = _now()
    plan = IntakePlan(status="enumerating", created_at=now, updated_at=now)
    _save(request_id, plan)
    threading.Thread(
        target=_enumerate_into,
        args=(request_id, plan.created_at),
        name=f"intake-plan-{request_id[:10]}",
        daemon=True,
    ).start()
    return to_view(plan, kind=row["kind"])


def update(request_id: str, body: IntakePlanUpdate) -> IntakePlanView:
    row = pending_intake(request_id)
    plan = stored_plan(row)
    if plan is None or plan.status != "ready":
        raise PlanError("build the plan before editing it", 409)
    edits = {e.key: sorted(set(e.chapters)) for e in body.entries}
    for entry in plan.entries:
        if entry.key in edits and edits[entry.key] != entry.chapters:
            entry.chapters = edits[entry.key]
            entry.confidence = "manual" if entry.chapters else "none"
    plan.identity = body.identity
    plan.updated_at = _now()
    _save(request_id, plan)
    return to_view(plan, kind=row["kind"])


def _enumerate_into(request_id: str, created_at: str) -> None:
    try:
        row = repo_requests.get_by_id(request_id)
        payload = _serde.json_loads(row["payload"]) or {}
        source = IntakeSource.model_validate(payload.get("source") or {"method": "links"})
        plan = _fresh_plan(row["kind"], payload, source, created_at)
    except Exception as exc:  # noqa: BLE001 — every failure is shown on the plan
        log.warning("intake plan %s: enumeration failed: %s", request_id, exc)
        message = str(exc) if isinstance(exc, _enumerate.EnumerationError) else repr(exc)
        plan = IntakePlan(
            status="failed", error=message[:500], created_at=created_at, updated_at=_now()
        )
    if not _save(request_id, plan, expect_created_at=created_at):
        log.info("intake plan %s: superseded before enumeration finished", request_id)


def _fresh_plan(kind: str, payload: dict, source: IntakeSource, created_at: str) -> IntakePlan:
    listing = _enumerate.enumerate_source(source)
    matches = _match.match_entries([e.title for e in listing.entries])
    entries: list[PlanEntry] = []
    for i, (raw, m) in enumerate(zip(listing.entries, matches, strict=True), 1):
        if listing.host == "links":
            chapters, confidence = sorted(raw.chapters), "exact"
        elif raw.unavailable:
            chapters, confidence = [], "none"
        else:
            chapters, confidence = list(m.chapters), m.confidence
        entries.append(
            PlanEntry(
                key=f"e{i}",
                url=raw.url,
                title=raw.title,
                index=raw.index,
                duration_sec=raw.duration_sec,
                chapters=chapters,
                confidence=cast(MatchConfidence, confidence),
            )
        )
    catalog = repo_catalog.snapshot()
    link_url = listing.source_url or (listing.entries[0].url if listing.entries else None)
    ident = _identity.propose(
        kind=kind,
        payload=payload,
        host=listing.host,
        source_url=link_url,
        uploader=listing.uploader,
        uploader_url=listing.uploader_url,
        catalog=catalog,
    )
    return IntakePlan(
        status="ready",
        host=cast(PlanHost, listing.host),
        source_url=listing.source_url,
        uploader=listing.uploader,
        uploader_url=listing.uploader_url,
        entries=entries,
        identity=ident,
        created_at=created_at,
        updated_at=_now(),
    )


# ---------------------------------------------------------------------------
# The live check
# ---------------------------------------------------------------------------


def to_view(plan: IntakePlan, *, kind: str) -> IntakePlanView:
    view = IntakePlanView(**plan.model_dump())
    if plan.status != "ready":
        return view
    catalog = repo_catalog.snapshot()
    view.channel_options = [PlanOption(slug=c.slug, label=c.name) for c in catalog.vocab.channels]
    view.source_options = [PlanOption(slug=s.slug, label=s.name) for s in catalog.vocab.sources]
    view.coverage, errors, warnings = check_entries(plan)
    errors += _identity.check(plan.identity, kind=kind, catalog=catalog)
    if plan.host == "youtube" and not youtube_cookies_configured():
        errors.append(
            "YouTube refuses downloads from Hugging Face without a signed-in session — "
            "set the INSPECTOR_YTDLP_COOKIES secret (a cookies.txt export) on the Space."
        )
    view.errors, view.warnings = errors, warnings
    return view


def check_entries(plan: IntakePlan) -> tuple[PlanCoverage, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    owner: dict[int, str] = {}
    duplicates: set[int] = set()
    combined = 0
    for e in plan.entries:
        label = f"#{e.index or e.key} {e.title[:60]}".strip()
        if not e.chapters:
            continue
        if any(not 1 <= c <= LAST_CHAPTER for c in e.chapters):
            errors.append(f"{label}: chapters must be 1–114.")
            continue
        if e.chapters != list(range(e.chapters[0], e.chapters[-1] + 1)):
            errors.append(f"{label}: one file can only hold consecutive chapters.")
        if len(e.chapters) > 1:
            combined += 1
        for c in e.chapters:
            if c in owner:
                duplicates.add(c)
            owner.setdefault(c, e.key)
    chapters = sorted(owner)
    missing = [c for c in range(1, LAST_CHAPTER + 1) if c not in owner]
    if duplicates:
        errors.append(
            "Chapters claimed by more than one entry: " + ", ".join(map(str, sorted(duplicates)))
        )
    if not chapters:
        errors.append("No entry is mapped to a chapter.")
    elif missing:
        warnings.append(f"{len(missing)} chapter(s) missing — the delivery will be partial.")
    low = sum(1 for e in plan.entries if e.confidence == "low" and e.chapters)
    if low:
        warnings.append(f"{low} low-confidence match(es) — check them before aligning.")
    long_files = [e for e in plan.entries if e.chapters and (e.duration_sec or 0) > LONG_FILE_S]
    if long_files:
        warnings.append(
            f"{len(long_files)} file(s) longer than {LONG_FILE_S // 3600} h — alignment of "
            "one very long file may fail; prefer per-surah files when the source has them."
        )
    skipped = sum(1 for e in plan.entries if not e.chapters)
    if skipped:
        warnings.append(f"{skipped} entr(ies) left out of the delivery.")
    coverage = PlanCoverage(
        chapters=chapters, missing=missing, duplicates=sorted(duplicates), combined_entries=combined
    )
    return coverage, errors, warnings


def youtube_cookies_configured() -> bool:
    import os

    return bool((os.environ.get("INSPECTOR_YTDLP_COOKIES") or "").strip())


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_s(iso: str) -> float:
    try:
        then = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return float(timedelta(days=1).total_seconds())
    return (datetime.now(UTC) - then).total_seconds()
