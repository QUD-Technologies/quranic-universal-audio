"""Submit-recitation **intake** service (slugless new-combo / new-reciter).

Submit and owner-resolve for the two contribution types that have no catalogued
delivery yet. Distinct from ``services.pending_requests`` (the slug-based
edit-request flow) — but resolution funnels through the same ``repo_requests``
core (``resolve`` / ``resolve_by_id``).

**There is no separate owner "accept" step.** A submission lands ``pending`` and
is immediately ingest-actionable: the owner's decision to *align* it IS the
acceptance. From the Requests tab the owner builds an intake plan
(``services/admin/intake_plan`` — enumerate the source, match files to chapters,
propose the catalog identity), reviews it, and clicks Align:
``intake_plan.mint.mint_and_align`` calls :func:`ingest` to create the reciter +
delivery + manifest and back-fill ``requests.slug`` (flipping the row to
``accepted`` — ``accepted`` means *ingested*), then starts the align run that
fetches, probes and aligns the audio. An owner can still ``resolve`` (send back /
discard) a pending submission they don't want.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import ValidationError

from qua_shared.schemas import (
    Actor,
    AudioCategory,
    AudioManifestSidecar,
    Channel,
    Delivery,
    IntakeSubmission,
    IntakeValidation,
    ProbeResponse,
    Source,
)
from qua_shared.schemas.config.state import SLUG_RE
from qua_shared.schemas.wire.intake_requests import IntakeSource
from services.db import _serde, repo_requests
from services.db import sync as _sync
from services.storage import storage_paths
from services.storage.hf_bucket import get_backend

from . import intake_validation
from .intake_probe import probe_source

logger = logging.getLogger(__name__)

_INTAKE_KINDS = ("existing_reciter_new_combo", "new_reciter")


class IntakeError(Exception):
    """Base for caller-facing intake failures (route maps to 400)."""


class IntakeValidationError(IntakeError):
    """Structural validation failed. Carries the :class:`IntakeValidation`."""

    def __init__(self, validation: IntakeValidation):
        super().__init__("; ".join(validation.errors) or "invalid submission")
        self.validation = validation


class NotIntakeRequest(IntakeError):
    """The id isn't a pending intake request (unknown / wrong kind / resolved)."""


class IngestError(IntakeError):
    """Base for ingest-mint failures."""


class IngestBadRequest(IngestError):
    """Malformed ingest body or the request row isn't an accepted slugless
    intake awaiting ingest (route maps to 400)."""


class IngestSlugCollision(IngestError):
    """The proposed delivery slug already exists (route maps to 409)."""


class IngestVocabMissing(IngestError):
    """A delivery FK (source/channel/riwayah/style/reciter) is still missing
    after applying ``vocab_additions`` (route maps to 422)."""


# ---- Submit -----------------------------------------------------------------


def submit(sub: IntakeSubmission, *, requester: Actor) -> tuple[str, IntakeValidation]:
    """Validate + record a slugless intake request. Returns ``(request_id,
    validation)``. Raises :class:`IntakeValidationError` if blocking errors
    exist. Warnings (missing chapters, dupes, likely-duplicate request) ride
    along on the returned validation."""
    validation = intake_validation.validate_submission(sub)
    _append_dedup_warning(sub, validation)
    if validation.errors:
        raise IntakeValidationError(validation)

    extra_payload = {
        "reciter_id": sub.reciter_id,
        "source": sub.source.model_dump(mode="json"),
        "attestations": sub.attestations.model_dump(mode="json"),
    }
    with _sync.durable_transaction():
        rid = repo_requests.submit(
            slug=None,
            requester=requester,
            proposed_edits=sub.proposed_edits,
            comments=sub.comments,
            auto_claim=sub.auto_claim,
            kind=sub.kind,
            extra_payload=extra_payload,
        )
        from services.state import audit

        audit.append(
            event="request.intake_submitted",
            actor=requester,
            payload={"request_id": rid, "kind": sub.kind, "reciter_id": sub.reciter_id},
        )
    _invalidate()
    _notify_owners_new_intake(sub, rid, requester=requester)
    return rid, validation


def _notify_owners_new_intake(sub: IntakeSubmission, rid: str, *, requester: Actor) -> None:
    """Owner-facing review alert for a slugless intake (best-effort, own txn).
    Resolves a display name (proposed name for new reciters, the existing
    reciter's name for new combos) and a short riwayah · style body. Wrapped so
    a lookup hiccup never breaks the submission."""
    try:
        from services.notifications import emit as notify_service
        from services.state import catalog as catalog_service

        proposed = sub.proposed_edits
        if sub.kind == "new_reciter":
            name = proposed.name_en or "a new reciter"
        else:  # existing_reciter_new_combo
            reciter = catalog_service.find_reciter(sub.reciter_id) if sub.reciter_id else None
            name = (reciter.name_en if reciter is not None else None) or "a new combination"
        combo = " · ".join(p for p in (proposed.riwayah, proposed.style) if p)
        notify_service.notify_owners_new_request(
            kind=sub.kind,
            reciter_name=name,
            requester=requester,
            slug=None,
            request_id=rid,
            body=combo or None,
        )
    except Exception:  # noqa: BLE001 — best-effort; never break the submission
        logger.exception("intake: owner new-request notification failed for rid=%s", rid)


def _append_dedup_warning(sub: IntakeSubmission, validation: IntakeValidation) -> None:
    """Non-blocking heads-up when a near-identical pending intake already exists
    (slugless rows aren't covered by ``ux_request_pending_slug``)."""
    rows = repo_requests.admin_list_rows(status="pending")
    for row in rows:
        if row["kind"] not in _INTAKE_KINDS or row["kind"] != sub.kind:
            continue
        payload = _serde.json_loads(row["payload"]) or {}
        edits = payload.get("proposed_edits") or {}
        if sub.kind == "existing_reciter_new_combo":
            if (
                payload.get("reciter_id") == sub.reciter_id
                and edits.get("riwayah") == sub.proposed_edits.riwayah
                and edits.get("style") == sub.proposed_edits.style
            ):
                validation.warnings.append(
                    "A pending request for this reciter + combination already exists."
                )
                return
        elif sub.kind == "new_reciter":
            name = (sub.proposed_edits.name_en or "").strip().lower()
            if name and (edits.get("name_en") or "").strip().lower() == name:
                validation.warnings.append(
                    f"A pending new-reciter request for '{sub.proposed_edits.name_en}' "
                    "already exists."
                )
                return


# ---- Ingest (owner, via the intake plan) ------------------------------------


def ingest(request_id: str, payload: dict, actor: Actor) -> dict:
    """Mint the catalog delivery for an accepted slugless intake and seed it
    into the alignment queue, in ONE durable transaction.

    Called in-process by ``intake_plan.mint`` before the align run fetches the
    audio (the run's acquire step then writes the probed audio rollup back onto
    the manifest + delivery). Steps (all atomic):

    1. Validate the request row is a slugless intake kind, ``slug IS NULL``, and
       not-yet-ingested (``status`` in ``pending`` / ``accepted``). There is no
       separate accept gate — a fresh ``pending`` submission is directly
       ingestable; ingest itself flips the row to ``accepted`` when it back-fills
       the slug (step 7). (Idempotent: a row that already has ``slug`` set was
       already ingested → 200 no-op with the existing slug.)
    2. Apply ``vocab_additions`` (idempotent ``add_source`` / ``add_channel``) so
       the delivery's source/channel FKs resolve.
    3. ``catalog.add_reciter`` when ``reciter`` is provided and new (idempotent on
       ``reciter_id``).
    4. ``catalog.add_delivery`` (constructs a :class:`Delivery`; ``chapter_count``
       derived from the manifest, ``added_*`` stamped from the actor + now).
    5. Write the ``catalog/audio_manifest/<slug>.json`` sidecar.
    6. ``state.transition(slug, "reciter.requested", ...)`` to seed
       ``AWAITING_ALIGNMENT`` + its pending entry — the SAME path
       ``existing_combo_edit`` uses, so ``auto_detect`` later flips it to
       ``AWAITING_REVIEW`` when the content folder is noticed.
    7. ``repo_requests.resolve_by_id`` to back-fill ``requests.slug``.

    Returns ``{"ok": True, "slug": str, "state": "awaiting_alignment"}``.

    Lazy ``catalog``/``state`` imports avoid an import cycle (state → catalog →
    this package at init).
    """
    from services.state import audit
    from services.state import catalog as catalog_service
    from services.state import state as state_service

    row = repo_requests.get_by_id(request_id)
    if row is None:
        raise NotIntakeRequest(request_id)
    if row["kind"] not in _INTAKE_KINDS:
        raise IngestBadRequest(
            f"request {request_id} is kind {row['kind']!r}, not a slugless intake"
        )
    if row["status"] not in ("pending", "accepted"):
        raise IngestBadRequest(
            f"request {request_id} is {row['status']!r}, not an ingestable intake "
            "(expected pending or accepted)"
        )
    # Idempotent: already ingested (slug back-filled) → no-op.
    if row["slug"]:
        existing_slug = row["slug"]
        r = state_service.get_row(existing_slug)
        return {
            "ok": True,
            "slug": existing_slug,
            "state": r.state.value if r is not None else None,
        }

    delivery_in = payload.get("delivery")
    if not isinstance(delivery_in, dict):
        raise IngestBadRequest("missing or malformed 'delivery'")
    slug = (delivery_in.get("slug") or "").strip()
    if not slug or not SLUG_RE.match(slug):
        raise IngestBadRequest(f"invalid delivery slug: {slug!r}")

    manifest_in = payload.get("audio_manifest")
    if not isinstance(manifest_in, dict):
        raise IngestBadRequest("missing or malformed 'audio_manifest'")
    chapters_in = manifest_in.get("chapters") or {}
    sources_in = manifest_in.get("sources") or []
    if not isinstance(chapters_in, dict) or not isinstance(sources_in, list):
        raise IngestBadRequest("malformed 'audio_manifest.chapters' / 'sources'")
    if not chapters_in and not sources_in:
        raise IngestBadRequest("'audio_manifest' needs chapters or sources")

    # Slug collision (409) — check BEFORE opening the txn so the response is a
    # clean 409 instead of a rolled-back IntegrityError.
    if catalog_service.find_delivery(slug) is not None:
        raise IngestSlugCollision(f"delivery slug {slug!r} already exists")

    try:
        audio_category = AudioCategory(delivery_in.get("audio_category"))
    except ValueError as e:
        raise IngestBadRequest(
            f"invalid audio_category: {delivery_in.get('audio_category')!r}"
        ) from e

    reason = payload.get("reason")
    reciter_in = payload.get("reciter")
    vocab_in = payload.get("vocab_additions")

    # Build + write the audio_manifest sidecar BEFORE the DB transaction. The
    # bucket is NOT part of the SQLite txn, so a write inside it would orphan the
    # sidecar on rollback (e.g. ``reciter.requested`` raising), or leave a
    # committed delivery with no manifest if it ran after commit (the idempotent
    # re-ingest no-op would never repair it). Writing first means a rollback only
    # ever leaves a harmless orphan keyed by a slug with no delivery — overwritten
    # verbatim on retry, never read in the meantime. Also keeps slow bucket I/O
    # out from under the serialized SQLite write lock.
    manifest = _build_manifest(slug, audio_category, chapters_in, sources_in)
    get_backend().write_json_atomic(
        storage_paths.audio_manifest_path(slug),
        manifest.model_dump(mode="json", by_alias=True),
    )

    with _sync.durable_transaction():
        # vocab additions (idempotent — satisfies add_delivery's FKs).
        _apply_vocab_additions(vocab_in, actor=actor, catalog_service=catalog_service)

        # reciter (idempotent on reciter_id).
        if isinstance(reciter_in, dict):
            rid = (reciter_in.get("reciter_id") or "").strip()
            if not rid:
                raise IngestBadRequest("reciter.reciter_id is required when 'reciter' is set")
            if catalog_service.find_reciter(rid) is None:
                catalog_service.add_reciter(
                    actor=actor,
                    reciter_id=rid,
                    name_en=reciter_in.get("name_en") or rid,
                    name_ar=reciter_in.get("name_ar"),
                    country=reciter_in.get("country"),
                    reason=reason,
                )

        # delivery — construct the Delivery model (never a dict literal).
        delivery = _build_delivery(
            delivery_in,
            audio_category=audio_category,
            chapter_count=len(chapters_in),
            actor=actor,
        )
        try:
            catalog_service.add_delivery(actor=actor, delivery=delivery, reason=reason)
        except catalog_service.InvalidCatalogChange as e:
            # add_delivery wraps BOTH a duplicate-slug and an FK violation. The
            # pre-txn find_delivery check normally catches collisions, so a
            # duplicate here is a writer race; disambiguate for the right status.
            if catalog_service.find_delivery(slug) is not None:
                raise IngestSlugCollision(f"delivery slug {slug!r} already exists") from e
            raise IngestVocabMissing(str(e)) from e

        # seed AWAITING_ALIGNMENT + pending entry (same path as existing_combo_edit).
        # Fire reciter.requested AS THE ORIGINAL REQUESTER, carrying their
        # auto_claim + proposed_edits + comments — the pending entry records the
        # requester from the transition actor (state._h_requested), and the
        # auto_claim self-review allocation in _maybe_auto_claim fires for THEM at
        # alignment_completed. Attributing it to the admin running the ingest would
        # both mis-credit the request and silently drop the self-review claim.
        req_payload = _serde.json_loads(row["payload"]) or {}
        requester_raw = req_payload.get("requester")
        requester = Actor.model_validate(requester_raw) if requester_raw else actor
        new_row = state_service.transition(
            slug,
            "reciter.requested",
            actor=requester,
            payload={
                "proposed_edits": req_payload.get("proposed_edits") or {},
                "comments": row["comments"],
                "auto_claim": bool(row["auto_claim"]),
            },
        )

        # 7. back-fill requests.slug.
        repo_requests.resolve_by_id(
            request_id=request_id,
            status="accepted",
            transitioned_by=actor,
            slug=slug,
        )

        audit.append(
            event="request.intake_ingested",
            actor=actor,
            slug=slug,
            payload={"request_id": request_id, "kind": row["kind"]},
            reason=reason,
        )

    _invalidate()
    return {"ok": True, "slug": slug, "state": new_row.state.value}


def _apply_vocab_additions(vocab_in, *, actor: Actor, catalog_service) -> None:
    """Idempotently add any source/channel vocab the delivery's FKs need."""
    if not isinstance(vocab_in, dict):
        return
    for src in vocab_in.get("sources") or []:
        if not isinstance(src, dict):
            continue
        try:
            source = Source(
                slug=src["slug"],
                name=src["name"],
                url=src.get("url"),
                audio_categories=src.get("audio_categories") or [],
            )
        except (KeyError, ValidationError) as e:
            raise IngestBadRequest(f"invalid vocab source: {e}") from e
        catalog_service.add_source(actor=actor, source=source)
    for ch in vocab_in.get("channels") or []:
        if not isinstance(ch, dict):
            continue
        try:
            channel = Channel(
                slug=ch["slug"],
                name=ch["name"],
                short=ch["short"],
                host_patterns=ch.get("host_patterns") or [],
            )
        except (KeyError, ValidationError) as e:
            raise IngestBadRequest(f"invalid vocab channel: {e}") from e
        catalog_service.add_channel(actor=actor, channel=channel)


def _build_delivery(
    delivery_in: dict, *, audio_category: AudioCategory, chapter_count: int, actor: Actor
) -> Delivery:
    """Construct the :class:`Delivery` model from the ingest body. ``chapter_count``
    is derived from the manifest; ``added_*`` are stamped server-side.

    The audio rollup (``bitrate_mode`` / ``bitrate_kbps_nominal`` /
    ``sample_rate_hz`` / ``total_duration_sec``) is optional — the online mint
    sends none (the audio is not fetched yet) and the align run's manifest sync
    fills it; absent, the Delivery defaults stand (``unknown`` / null)."""
    # Only override the audio-rollup defaults the body actually carries — passing
    # bitrate_mode=None would fail the enum, and None kbps/rate/duration are the
    # model defaults anyway.
    rollup = {
        k: delivery_in[k]
        for k in ("bitrate_mode", "bitrate_kbps_nominal", "sample_rate_hz", "total_duration_sec")
        if delivery_in.get(k) is not None
    }
    try:
        return Delivery(
            slug=delivery_in["slug"],
            reciter_id=delivery_in["reciter_id"],
            riwayah=delivery_in["riwayah"],
            style=delivery_in["style"],
            source=delivery_in["source"],
            channel=delivery_in["channel"],
            source_url=delivery_in.get("source_url"),
            audio_category=audio_category,
            chapter_count=chapter_count,
            recording_context=delivery_in.get("recording_context"),
            recording_year=delivery_in.get("recording_year"),
            added_at=datetime.now(UTC),
            added_by_hf_id=actor.hf_user_id,
            **rollup,
        )
    except (KeyError, ValidationError) as e:
        raise IngestBadRequest(f"invalid delivery: {e}") from e


def _build_manifest(
    slug: str, audio_category: AudioCategory, chapters_in: dict, sources_in: list | None = None
) -> AudioManifestSidecar:
    """Build the audio_manifest sidecar model from the ingest chapter map (and
    the source files whose chapters the align run will detect).

    The checksum is a stable digest of the sorted ``(key, url)`` pairs so a
    re-ingest of the same chapters produces the same ``_meta.checksum``."""
    chapters: dict[str, dict] = {}
    for key, entry in chapters_in.items():
        if not isinstance(entry, dict):
            raise IngestBadRequest(f"audio_manifest chapter {key!r} is not an object")
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            raise IngestBadRequest(f"audio_manifest chapter {key!r} missing url")
        chapters[str(key)] = {
            "url": url,
            "size_bytes": entry.get("size_bytes"),
            "duration_sec": (
                int(round(entry["duration_sec"]))
                if isinstance(entry.get("duration_sec"), (int, float))
                else None
            ),
            "bitrate_kbps": entry.get("bitrate_kbps"),
            "bitrate_mode": entry.get("bitrate_mode"),
            # Combined-file provenance: original source URL + the chapter's
            # offset within it, preserved when ``url`` was swapped for a
            # per-chapter bucket path. Both null for normal chapters.
            "source_url": entry.get("source_url"),
            "source_offset_ms": (
                int(round(entry["source_offset_ms"]))
                if isinstance(entry.get("source_offset_ms"), (int, float))
                else None
            ),
        }
    from services.audio.audio_meta import manifest_checksum

    checksum = manifest_checksum(chapters)
    try:
        return AudioManifestSidecar.model_validate(
            {
                "slug": slug,
                "chapters": chapters,
                "sources": sources_in or [],
                "_meta": {
                    "checksum": checksum,
                    "chapter_count": len(chapters),
                    "category": audio_category.value,
                },
            }
        )
    except ValidationError as e:
        raise IngestBadRequest(f"invalid audio_manifest: {e}") from e


# ---- Resolve: send back / discard (owner) -----------------------------------


def resolve(request_id: str, *, status: str, reason: str, actor: Actor) -> None:
    """Send back (``returned``) or discard (``discarded``) a pending intake
    request. Pure request-status mutation — slugless rows have no state row, so
    no state-machine transition fires (unlike edit-request rejects)."""
    if status not in ("returned", "discarded"):
        raise IntakeError(f"invalid resolve status: {status!r}")
    row = _require_pending_intake(request_id)
    # Capture the requester + proposed name for the notification — slugless
    # rows have no catalog entry, so the name comes off the request payload.
    requester_id = row["requester_id"]
    req_payload = _serde.json_loads(row["payload"]) or {}
    edits = req_payload.get("proposed_edits") or {}
    reciter_name = edits.get("name_en") or req_payload.get("reciter_id") or "your submission"
    with _sync.durable_transaction():
        repo_requests.resolve_by_id(
            request_id=request_id,
            status=status,
            transitioned_by=actor,
            reason=reason,
        )
        from services.state import audit

        rec = audit.append(
            event=f"request.intake_{status}",
            actor=actor,
            payload={"request_id": request_id},
            reason=reason,
        )
        from services.db.connection import get_conn
        from services.notifications import emit as _notify

        _notify.emit_for_event(
            get_conn(),
            rec,
            extra={"requester_id": requester_id, "reciter_name": reciter_name},
        )
    _invalidate()


# ---- Probe (owner) ----------------------------------------------------------


def probe(request_id: str) -> ProbeResponse:
    """Reachability-probe a pending intake request's source and cache the result
    onto ``payload.probe``."""
    row = _require_pending_intake(request_id)
    payload = _serde.json_loads(row["payload"]) or {}
    src = IntakeSource.model_validate(payload.get("source") or {"method": "links"})
    result = probe_source(src)
    with _sync.durable_transaction():
        payload["probe"] = result.model_dump(mode="json")
        repo_requests.set_payload(request_id, payload)
    _invalidate()
    return result


# ---- Helpers ----------------------------------------------------------------


def _require_pending_intake(request_id: str):
    row = repo_requests.get_by_id(request_id)
    if row is None or row["kind"] not in _INTAKE_KINDS or row["status"] != "pending":
        raise NotIntakeRequest(request_id)
    return row


def _invalidate() -> None:
    from services.storage import cache as _cache

    _cache.invalidate_admin_requests_cache()
