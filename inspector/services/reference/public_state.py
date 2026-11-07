"""Public state mapper for the Phase 6 dashboard.

Translates the internal ``ReciterRow`` + catalog delivery + reciter into the
six-bucket public taxonomy and strips assignee identity from every payload.
Never reveals slugs in user-facing copy — the slug is included only as a
stable client-side ID for grouping / picker selection, never rendered to the
user. See ``inspector/CLAUDE.md`` for the slug-exposure rule.

The public surface aggregates by reciter (one row per ``reciter_id``) and
embeds the reciter's deliveries inline. The dashboard frontend renders one
reciter row + an inline-expandable deliveries sub-table, so backend doing
the aggregation keeps the UI thin and the data shape unambiguous.

Authoritative spec:
- Phase 6 contract: docs/planning/inspector-deploy/v2/phases/06-public-dashboard.md
- Public taxonomy + activity feed: §1, §"Public activity feed"

Slice B of phase 6.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NotRequired, TypedDict

from qua_shared.catalog_visibility import is_everyayah_channel
from qua_shared.schemas import (
    AudioCategory,
    Delivery,
    ReciterEntry,
    ReciterRow,
    ReciterState,
    Visibility,
)
from services.state import catalog as catalog_service
from services.state import state as state_service

PublicBucket = Literal[
    "available_for_request",
    "requested",
    "available_for_review",
    "under_review",
    "published",
]

# Order matters — used to compute "primary_bucket" as the most-progressed
# bucket across a reciter's deliveries. Higher index = more progressed.
_BUCKET_PROGRESS: dict[PublicBucket, int] = {
    "available_for_request": 0,
    "requested": 1,
    "available_for_review": 2,
    "under_review": 3,
    "published": 4,
}

# Internal lifecycle state → public bucket. CATALOGUED reads as
# still-requestable. Single source of truth for both ``bucket_for`` (current
# state) and the transitions replay in ``_bucket_dates_for_slug`` (historical
# states). The legacy ``awaiting_timestamps`` state is gone; any historical
# transition row carrying that string is skipped by the replay's ValueError
# guard (it collapsed onto "under_review" anyway).
_STATE_TO_BUCKET: dict[ReciterState, PublicBucket] = {
    ReciterState.CATALOGUED: "available_for_request",
    ReciterState.AWAITING_ALIGNMENT: "requested",
    ReciterState.AWAITING_REVIEW: "available_for_review",
    ReciterState.UNDER_REVIEW: "under_review",
    ReciterState.RELEASED: "published",
}

# Full-mushaf chapter counts per audio category. Anything less is "partial".
_FULL_CHAPTERS: dict[AudioCategory, int] = {
    AudioCategory.BY_SURAH: 114,
    AudioCategory.BY_AYAH: 6236,
}


# ---------- types (TypedDicts for docs; runtime returns plain dicts) ----------


class PublicDelivery(TypedDict):
    slug: str  # ID only — never rendered to users.
    bucket: PublicBucket
    state_since: str | None  # ISO datetime; None when no state row exists.
    riwayah: str
    style: str
    recording_context: str | None
    recording_year: int | None
    source: str
    channel: str
    channel_name: str  # human display name from vocab; falls back to slug
    source_url: (
        str | None
    )  # originating playlist URL (download-only sources); channel hyperlink target
    audio_category: str  # by_surah | by_ayah
    chapter_count: int
    coverage_kind: Literal["full", "partial"]
    bitrate_kbps_nominal: int | None
    bitrate_mode: str
    total_duration_sec: int | None
    # Per-bucket entry timestamps: {bucket: [iso, ...]} chronological, one entry
    # per *distinct* visit to that bucket. Modal-only — attached by the detail /
    # admin-view paths (NOT the cached list), so it's absent on list payloads.
    bucket_dates: NotRequired[dict[str, list[str]]]
    # Timestamp-regeneration dates: every TS generation after the first, ISO
    # ascending. Empty when never regenerated. Modal-only (same paths as
    # ``bucket_dates``) — drives the conditional "Timestamps refreshed" node.
    ts_refresh_dates: NotRequired[list[str]]


class PublicReciter(TypedDict):
    reciter_id: str  # ID only.
    name: str  # display (en)
    name_ar: str | None
    country: str | None
    primary_bucket: PublicBucket
    buckets: list[PublicBucket]  # union across deliveries
    deliveries: list[PublicDelivery]
    riwayat: list[str]
    styles: list[str]
    recording_contexts: list[str]  # null entries dropped at the producer
    sources: list[str]
    channels: list[str]
    chapter_count_total: int
    deliveries_count: int
    coverage_kind: Literal["full", "partial", "mixed"]
    last_activity: str | None  # max state_since across deliveries


class AdminDelivery(PublicDelivery):
    """Full editable delivery metadata for maintainer/owner modal views."""

    variant_label: str | None
    codec: str
    container: str
    sample_rate_hz: int | None
    channels: int | None
    visibility: str
    visibility_reason: str | None
    cleanup_status: str | None


# ---------- core mappers ----------


def bucket_for(row: ReciterRow | None) -> PublicBucket:
    """Translate an internal state row to a public bucket.

    ``None`` means the catalog has a delivery but no state row has been
    created — that's ``available_for_request`` (a "suggest this reciter"
    affordance in the UI).

    A row in ``CATALOGUED`` likewise maps to ``available_for_request``: the
    maintainer has seeded the row but no one has fired ``reciter.requested``
    yet, so it's still requestable. ``AWAITING_ALIGNMENT`` is the first
    state that maps to the ``requested`` pill (i.e. someone has actually
    submitted a request and the pending entry is in flight).
    """
    if row is None:
        return "available_for_request"
    try:
        # marked_ready does not change the bucket; an under_review reciter reads
        # as "under_review" until its timestamps job publishes it RELEASED. The
        # flag itself rides separately as ``PublicDelivery.marked_ready``.
        return _STATE_TO_BUCKET[row.state]
    except KeyError:
        # Defensive — new ReciterState members must be added to _STATE_TO_BUCKET
        # (callers depend on the closed set).
        raise ValueError(f"unmapped reciter state: {row.state!r}") from None


def _delivery_coverage(d: Delivery) -> Literal["full", "partial"]:
    full = _FULL_CHAPTERS.get(d.audio_category)
    if full is None or d.chapter_count < full:
        return "partial"
    return "full"


def _to_public_delivery(
    d: Delivery,
    row: ReciterRow | None,
    channel_names: dict[str, str],
) -> PublicDelivery:
    return PublicDelivery(
        slug=d.slug,
        bucket=bucket_for(row),
        state_since=(row.state_since.isoformat() if row is not None else None),
        riwayah=d.riwayah,
        style=d.style,
        recording_context=d.recording_context,
        recording_year=d.recording_year,
        source=d.source,
        channel=d.channel,
        channel_name=channel_names.get(d.channel, d.channel),
        source_url=d.source_url,
        audio_category=d.audio_category.value,
        chapter_count=d.chapter_count,
        coverage_kind=_delivery_coverage(d),
        bitrate_kbps_nominal=d.bitrate_kbps_nominal,
        bitrate_mode=d.bitrate_mode.value
        if hasattr(d.bitrate_mode, "value")
        else str(d.bitrate_mode),
        total_duration_sec=d.total_duration_sec,
        marked_ready=bool(row is not None and row.marked_ready),
    )


def _bucket_dates_for_slug(slug: str) -> dict[str, list[str]]:
    """Replay a delivery's transitions into per-bucket entry timestamps.

    Returns ``{bucket: [iso, ...]}`` in chronological order — one timestamp per
    *distinct* entry into that bucket. Consecutive transitions that stay in the
    same bucket collapse to a single entry; a bucket re-entered after regressing
    away (e.g. under_review → available_for_review → under_review) records a
    fresh timestamp. Buckets never entered are absent. Historical
    ``awaiting_timestamps`` rows (a removed state) hit the ValueError guard
    below and are skipped.

    Modal-only: called from the per-open ``detail`` / ``admin_view`` paths, one
    indexed ``transitions`` query per delivery — never the cached list path.
    """
    from services.db import repo_transitions

    out: dict[str, list[str]] = {}
    prev_bucket: PublicBucket | None = None
    for rec in repo_transitions.for_slug(slug):
        raw = rec.get("to_state")
        if not raw:
            continue
        try:
            bucket = _STATE_TO_BUCKET[ReciterState(raw)]
        except (ValueError, KeyError):
            continue  # unknown/legacy state value — skip defensively
        if bucket == prev_bucket:
            continue  # same bucket, internal-only move — not a new visit
        out.setdefault(bucket, []).append(rec["ts"])
        prev_bucket = bucket
    return out


def _ts_refresh_dates_for_slug(slug: str) -> list[str]:
    """Timestamp-regeneration dates for a delivery — every TS generation after
    the first.

    Each ``per_recitation_releases(track='ts')`` row (current + superseded) is
    one generation; the first is the initial publish, the rest are refreshes
    (``complete_timestamps_job`` inserts a fresh row per regen). Returns the
    refresh ``produced_at`` values ascending; empty when never regenerated.
    """
    from services.db import repo_releases

    produced = [
        r["produced_at"]
        for r in repo_releases.all_releases_for_track_slug("ts", slug)
        if r.get("produced_at")
    ]
    return produced[1:]


def _attach_bucket_dates(deliveries: Sequence[PublicDelivery]) -> None:
    """Populate ``bucket_dates`` + ``ts_refresh_dates`` on each delivery in place
    (modal paths only)."""
    for d in deliveries:
        d["bucket_dates"] = _bucket_dates_for_slug(d["slug"])
        d["ts_refresh_dates"] = _ts_refresh_dates_for_slug(d["slug"])


def _primary_bucket(buckets: list[PublicBucket]) -> PublicBucket:
    if not buckets:
        return "available_for_request"
    return max(buckets, key=lambda b: _BUCKET_PROGRESS[b])


def _coverage_rollup(
    deliveries: Sequence[PublicDelivery],
) -> Literal["full", "partial", "mixed"]:
    if not deliveries:
        return "partial"
    kinds = {d["coverage_kind"] for d in deliveries}
    if kinds == {"full"}:
        return "full"
    if kinds == {"partial"}:
        return "partial"
    return "mixed"


def _unique_ordered(values: list) -> list:
    """Order-preserving dedupe; preserves the first-seen order across deliveries."""
    seen = set()
    out = []
    for v in values:
        key = v  # hashable (strings, None)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _channel_name_map() -> dict[str, str]:
    catalog = catalog_service.snapshot()
    return {ch.slug: ch.name for ch in catalog.vocab.channels}


def to_public_reciter(
    reciter: ReciterEntry,
    deliveries: list[Delivery],
    state_index: dict[str, ReciterRow],
    channel_names: dict[str, str] | None = None,
    *,
    include_everyayah: bool = False,
) -> PublicReciter:
    """Aggregate one reciter's catalog + state into a single public payload.

    ``state_index`` maps ``delivery_slug → ReciterRow`` and is built once at
    the call site so this fn stays O(deliveries). Deliveries whose slug isn't
    in ``state_index`` map to ``available_for_request``.

    Visibility: deliveries with ``visibility=discarded`` on their state row
    are excluded entirely (admin-only listing surfaces them via a separate
    code path in Phase 7).
    """
    if channel_names is None:
        channel_names = _channel_name_map()
    public_dels: list[PublicDelivery] = []
    for d in deliveries:
        if not include_everyayah and is_everyayah_channel(d.channel):
            continue
        row = state_index.get(d.slug)
        if row is not None and row.visibility != Visibility.PUBLIC:
            continue
        public_dels.append(_to_public_delivery(d, row, channel_names))

    buckets = _unique_ordered([d["bucket"] for d in public_dels])
    last_activity_values = [d["state_since"] for d in public_dels if d["state_since"]]
    last_activity = max(last_activity_values) if last_activity_values else None

    return PublicReciter(
        reciter_id=reciter.reciter_id,
        name=reciter.name_en,
        name_ar=reciter.name_ar,
        country=reciter.country,
        primary_bucket=_primary_bucket(buckets),
        buckets=buckets,
        deliveries=public_dels,
        riwayat=_unique_ordered([d["riwayah"] for d in public_dels]),
        styles=_unique_ordered([d["style"] for d in public_dels]),
        recording_contexts=_unique_ordered(
            [d["recording_context"] for d in public_dels if d["recording_context"]]
        ),
        sources=_unique_ordered([d["source"] for d in public_dels]),
        channels=_unique_ordered([d["channel"] for d in public_dels]),
        chapter_count_total=sum(d["chapter_count"] for d in public_dels),
        deliveries_count=len(public_dels),
        coverage_kind=_coverage_rollup(public_dels),
        last_activity=last_activity,
    )


# ---------- list / stats helpers ----------


def _build_state_index() -> dict[str, ReciterRow]:
    """Snapshot the state table (DB via ``state_service``) into a slug-keyed index."""
    return {row.slug: row for row in state_service.all_rows()}


def all_public_reciters(*, include_everyayah: bool = False) -> list[PublicReciter]:
    """Materialize every catalog reciter as a public payload.

    A reciter with zero deliveries (theoretically possible at catalog-edit
    boundaries) is skipped — the dashboard has nothing to render for it.

    Result is cached keyed on ``db_seq`` (the only high-frequency public path):
    a hit skips the full catalog-model (DB→pydantic) + state-JOIN rebuild. Any
    committed write bumps ``db_seq`` and transparently invalidates the entry.
    A shallow copy is returned so callers may sort/filter in place without
    mutating the cached canonical list.
    """
    from services import db as _db
    from services.storage import cache as _cache

    seq = _db.current_db_seq()
    cached = _cache.get_public_reciters_cache(seq, include_everyayah)
    if cached is not None:
        return list(cached)

    catalog = catalog_service.snapshot()
    state_index = _build_state_index()
    channel_names = {ch.slug: ch.name for ch in catalog.vocab.channels}

    # Group deliveries by reciter_id once.
    by_reciter: dict[str, list[Delivery]] = {}
    for d in catalog.deliveries:
        by_reciter.setdefault(d.reciter_id, []).append(d)

    out: list[PublicReciter] = []
    for reciter in catalog.reciters:
        dels = by_reciter.get(reciter.reciter_id, [])
        if not dels:
            continue
        public = to_public_reciter(
            reciter,
            dels,
            state_index,
            channel_names,
            include_everyayah=include_everyayah,
        )
        # If every delivery was discarded, the reciter has no public deliveries
        # — also skip; it would render an empty row.
        if not public["deliveries"]:
            continue
        out.append(public)

    _cache.set_public_reciters_cache(seq, out, include_everyayah)
    return list(out)


def detail(reciter_id: str, *, include_everyayah: bool = False) -> PublicReciter | None:
    """Materialize a single reciter for the public detail page.

    Returns ``None`` when the reciter_id isn't in the catalog, or when
    the reciter has no public deliveries (all discarded).
    """
    catalog = catalog_service.snapshot()
    reciter = next(
        (r for r in catalog.reciters if r.reciter_id == reciter_id),
        None,
    )
    if reciter is None:
        return None
    deliveries = [d for d in catalog.deliveries if d.reciter_id == reciter_id]
    if not deliveries:
        return None
    state_index = _build_state_index()
    channel_names = {ch.slug: ch.name for ch in catalog.vocab.channels}
    public = to_public_reciter(
        reciter,
        deliveries,
        state_index,
        channel_names,
        include_everyayah=include_everyayah,
    )
    if not public["deliveries"]:
        return None
    # Modal renders a per-delivery lifecycle timeline; the cached list path
    # (to_public_reciter) deliberately omits this heavier per-slug replay.
    _attach_bucket_dates(public["deliveries"])
    return public


class AdminViewDelivery(AdminDelivery):
    """Admin-view delivery: same fields as PublicDelivery plus visibility + reason
    so the reciter modal can render a separate ``Discarded`` section.
    """

    visibility: str  # "public" | "discarded"
    visibility_reason: str | None
    cleanup_status: str | None


class AdminViewReciter(TypedDict):
    """Like ``PublicReciter`` but the discarded combos are pulled into a
    separate list so the frontend can render them in a dedicated section
    (only visible to maintainers + owners). The ``deliveries`` list still
    contains only PUBLIC-visible combos, so primary_bucket/coverage rollups
    stay consistent with what end-users see."""

    reciter_id: str
    name: str
    name_ar: str | None
    country: str | None
    notes: str | None
    primary_bucket: PublicBucket
    buckets: list[PublicBucket]
    deliveries: list[AdminDelivery]
    discarded_deliveries: list[AdminViewDelivery]
    riwayat: list[str]
    styles: list[str]
    recording_contexts: list[str]
    sources: list[str]
    channels: list[str]
    chapter_count_total: int
    deliveries_count: int
    coverage_kind: Literal["full", "partial", "mixed"]
    last_activity: str | None
    fully_discarded: bool


def _to_admin_discarded_delivery(
    d: Delivery,
    row: ReciterRow,
    channel_names: dict[str, str],
) -> AdminViewDelivery:
    """Like ``_to_public_delivery`` but adds visibility fields. Caller guarantees
    ``row.visibility == DISCARDED``."""
    base = _to_public_delivery(d, row, channel_names)
    return AdminViewDelivery(
        **base,
        variant_label=d.variant_label,
        codec=d.codec,
        container=d.container,
        sample_rate_hz=d.sample_rate_hz,
        channels=d.channels,
        visibility=row.visibility.value,
        visibility_reason=row.visibility_reason,
        cleanup_status=_discard_cleanup_status(row.slug),
    )


def _discard_cleanup_status(slug: str) -> str | None:
    from services.db import repo_discard_cleanup

    cleanup = repo_discard_cleanup.get(slug)
    if cleanup is None:
        return None
    return cleanup.get("status")


def _to_admin_public_delivery(
    d: Delivery,
    row: ReciterRow | None,
    channel_names: dict[str, str],
) -> AdminDelivery:
    return AdminDelivery(
        **_to_public_delivery(d, row, channel_names),
        variant_label=d.variant_label,
        codec=d.codec,
        container=d.container,
        sample_rate_hz=d.sample_rate_hz,
        channels=d.channels,
        visibility="public",
        visibility_reason=None,
        cleanup_status=None,
    )


def admin_view_reciter(
    reciter_id: str,
    *,
    include_everyayah: bool = False,
) -> AdminViewReciter | None:
    """Materialize a single reciter for admin viewers (maintainer + owner).

    Surfaces PUBLIC combos in ``deliveries`` (same shape as the public
    detail endpoint) AND discarded combos in ``discarded_deliveries`` so
    the modal can render the latter in a dedicated section. ``fully_discarded``
    is true iff every combo is discarded — derived at read time, no state
    flag needed.
    """
    catalog = catalog_service.snapshot()
    reciter = next(
        (r for r in catalog.reciters if r.reciter_id == reciter_id),
        None,
    )
    if reciter is None:
        return None
    deliveries = [d for d in catalog.deliveries if d.reciter_id == reciter_id]
    if not deliveries:
        return None

    state_index = _build_state_index()
    channel_names = {ch.slug: ch.name for ch in catalog.vocab.channels}

    public_dels: list[AdminDelivery] = []
    discarded_dels: list[AdminViewDelivery] = []
    for d in deliveries:
        if not include_everyayah and is_everyayah_channel(d.channel):
            continue
        row = state_index.get(d.slug)
        if row is not None and row.visibility == Visibility.DISCARDED:
            discarded_dels.append(_to_admin_discarded_delivery(d, row, channel_names))
        else:
            public_dels.append(_to_admin_public_delivery(d, row, channel_names))

    if not public_dels and not discarded_dels:
        return None

    fully_discarded = len(public_dels) == 0 and len(discarded_dels) > 0

    # Modal renders a per-delivery lifecycle timeline (incl. discarded combos).
    _attach_bucket_dates(public_dels)
    _attach_bucket_dates(discarded_dels)

    buckets = _unique_ordered([d["bucket"] for d in public_dels])
    last_activity_values = [d["state_since"] for d in public_dels if d["state_since"]]
    last_activity = max(last_activity_values) if last_activity_values else None

    return AdminViewReciter(
        reciter_id=reciter.reciter_id,
        name=reciter.name_en,
        name_ar=reciter.name_ar,
        country=reciter.country,
        notes=reciter.notes,
        primary_bucket=_primary_bucket(buckets),
        buckets=buckets,
        deliveries=public_dels,
        discarded_deliveries=discarded_dels,
        riwayat=_unique_ordered([d["riwayah"] for d in public_dels]),
        styles=_unique_ordered([d["style"] for d in public_dels]),
        recording_contexts=_unique_ordered(
            [d["recording_context"] for d in public_dels if d["recording_context"]]
        ),
        sources=_unique_ordered([d["source"] for d in public_dels]),
        channels=_unique_ordered([d["channel"] for d in public_dels]),
        chapter_count_total=sum(d["chapter_count"] for d in public_dels),
        deliveries_count=len(public_dels),
        coverage_kind=_coverage_rollup(public_dels),
        last_activity=last_activity,
        fully_discarded=fully_discarded,
    )


def is_reciter_fully_discarded(reciter_id: str) -> bool:
    """True iff every delivery of ``reciter_id`` is discarded.

    Computed at read time from per-combo visibility — no separate
    reciter-level flag to keep in sync. Adding a new public combo
    auto-undiscards the reciter.
    """
    catalog = catalog_service.snapshot()
    deliveries = [d for d in catalog.deliveries if d.reciter_id == reciter_id]
    if not deliveries:
        return False
    state_index = _build_state_index()
    for d in deliveries:
        row = state_index.get(d.slug)
        if row is None or row.visibility == Visibility.PUBLIC:
            return False
    return True


def stats(*, include_everyayah: bool = False) -> dict[str, int]:
    """Counts of reciters per public bucket — primary_bucket only.

    Buckets are mutually exclusive at the reciter level (each reciter has
    exactly one ``primary_bucket``), so the counts sum to the total number
    of public reciters.
    """
    counts: dict[str, int] = {b: 0 for b in _BUCKET_PROGRESS}
    for r in all_public_reciters(include_everyayah=include_everyayah):
        counts[r["primary_bucket"]] += 1
    return counts
