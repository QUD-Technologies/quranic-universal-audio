"""Public dashboard ``/api/public/*`` wire schemas.

Response models for the read-only public catalog surface served by
``inspector/routes/public/public.py`` and built in
``inspector/services/reference/public_state.py``. They model the exact dict
shape the routes emit (the source of truth).

Shapes
------
- ``PublicBucket`` — the public lifecycle taxonomy the FE switches on. Five
  buckets only; the route's ``_VALID_BUCKETS`` filter allowlist additionally
  accepts a phantom ``"publishing"`` that no payload ever carries (no
  ``_STATE_TO_BUCKET`` mapping produces it) — it is intentionally NOT part of
  this type.
- ``PublicDelivery`` — one (reciter, riwayah, style, source, channel) combo.
  ``bucket_dates`` / ``ts_refresh_dates`` are attached only on the modal
  (detail / admin-view) paths, absent on the cached list — modelled optional.
- ``PublicReciter`` — one reciter row with its public deliveries inline +
  rolled-up facets. Served by ``GET /api/public/reciters`` (paginated) and
  ``GET /api/public/reciter/<id>`` (anonymous / contributor callers).
- ``AdminDiscardedDelivery`` — a ``PublicDelivery`` plus ``visibility`` +
  ``visibility_reason``; only appears in ``AdminViewReciter.discarded_deliveries``.
- ``AdminViewReciter`` — the maintainer / owner shape of
  ``GET /api/public/reciter/<id>``: public combos in ``deliveries`` plus the
  hidden combos pulled into ``discarded_deliveries`` + a ``fully_discarded`` flag.
- ``PublicReciterPage`` — the ``{reciters, total, next_cursor}`` envelope of
  ``GET /api/public/reciters``.
- ``BucketCounts`` — the per-bucket reciter tally of ``GET /api/public/stats``.

Response models, so ``ConfigDict(extra="forbid")`` (we own the producer). Dump
with ``exclude_none=True`` so the modal-only optionals vanish on list payloads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

# Public lifecycle taxonomy. Five buckets — the closed set the FE renders and
# switches on. The route filter allowlist (``_VALID_BUCKETS``) also accepts a
# phantom ``"publishing"`` that is never produced; it is excluded here on
# purpose (see module docstring).
PublicBucket = Literal[
    "available_for_request",
    "requested",
    "available_for_review",
    "under_review",
    "published",
]

# Delivery-level coverage: a single combo is either full-mushaf or partial.
DeliveryCoverageKind = Literal["full", "partial"]

# Reciter-level coverage rollup adds ``mixed`` (some full + some partial combos).
ReciterCoverageKind = Literal["full", "partial", "mixed"]

# Per-combo visibility flag (orthogonal to the lifecycle bucket).
Visibility = Literal["public", "discarded"]
DiscardCleanupStatus = Literal["pending", "failed", "completed", "restored"]


class PublicDelivery(BaseModel):
    """One reciter delivery (riwayah × style × source × channel combo).

    Mirrors ``_to_public_delivery`` in ``services/reference/public_state.py``.
    ``slug`` is an internal grouping ID only — never rendered to users.
    ``bucket_dates`` / ``ts_refresh_dates`` are attached only by the detail /
    admin-view paths (``_attach_bucket_dates``), so they are absent on the
    cached list payload — dump with ``exclude_none=True``.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str
    bucket: PublicBucket
    state_since: str | None = None
    riwayah: str
    style: str
    recording_context: str | None = None
    recording_year: int | None = None
    source: str
    channel: str
    channel_name: str
    source_url: str | None = None
    audio_category: str  # by_surah | by_ayah
    chapter_count: int
    coverage_kind: DeliveryCoverageKind
    bitrate_kbps_nominal: int | None = None
    bitrate_mode: str  # cbr | vbr | mixed | unknown
    total_duration_sec: int | None = None
    #: Claimer marked it ready for review. Public flag only — the claimer's
    #: identity stays admin-gated (``reviews.view``).
    marked_ready: bool = False

    # Modal-only (detail / admin-view) — absent on the list payload.
    bucket_dates: dict[str, list[str]] | None = None
    ts_refresh_dates: list[str] | None = None


class AdminDelivery(PublicDelivery):
    """Full delivery metadata exposed only in the admin reciter modal."""

    model_config = ConfigDict(extra="forbid")

    variant_label: str | None = None
    codec: str
    container: str
    sample_rate_hz: int | None = None
    channels: int | None = None
    visibility: Visibility = "public"
    visibility_reason: str | None = None
    cleanup_status: DiscardCleanupStatus | None = None


class AdminDiscardedDelivery(PublicDelivery):
    """A discarded combo surfaced in the admin reciter view.

    Same fields as ``PublicDelivery`` plus the visibility metadata so the modal
    can label why the combo was hidden. Mirrors
    ``services.reference.public_state.AdminViewDelivery``.
    """

    model_config = ConfigDict(extra="forbid")

    variant_label: str | None = None
    codec: str
    container: str
    sample_rate_hz: int | None = None
    channels: int | None = None

    visibility: Visibility
    visibility_reason: str | None = None
    cleanup_status: DiscardCleanupStatus | None = None


class PublicReciter(BaseModel):
    """One reciter aggregated for the public dashboard.

    Mirrors ``to_public_reciter`` in ``services/reference/public_state.py``.
    ``reciter_id`` is an internal lookup key — never rendered. ``deliveries``
    carries only PUBLIC-visible combos; the rolled-up facet lists
    (``riwayat`` / ``styles`` / …) are order-preserving dedupes across them.
    """

    model_config = ConfigDict(extra="forbid")

    reciter_id: str
    name: str
    name_ar: str | None = None
    country: str | None = None
    primary_bucket: PublicBucket
    buckets: list[PublicBucket]
    deliveries: list[PublicDelivery]
    riwayat: list[str]
    styles: list[str]
    recording_contexts: list[str]
    sources: list[str]
    channels: list[str]
    chapter_count_total: int
    deliveries_count: int
    coverage_kind: ReciterCoverageKind
    last_activity: str | None = None


class AdminViewReciter(PublicReciter):
    """Maintainer / owner shape of ``GET /api/public/reciter/<id>``.

    Extends ``PublicReciter`` with the hidden combos pulled into a dedicated
    ``discarded_deliveries`` array (so ``deliveries`` + the rollups stay
    identical to what end-users see) and a ``fully_discarded`` flag derived at
    read time. Mirrors ``services.reference.public_state.AdminViewReciter``.
    """

    model_config = ConfigDict(extra="forbid")

    notes: str | None = None
    deliveries: list[AdminDelivery]
    discarded_deliveries: list[AdminDelivery]
    fully_discarded: bool


class PublicReciterPage(BaseModel):
    """Paginated envelope of ``GET /api/public/reciters``.

    ``next_cursor`` is the integer offset of the next page, ``None`` on the
    last page.
    """

    model_config = ConfigDict(extra="forbid")

    reciters: list[PublicReciter]
    total: int
    next_cursor: int | None = None


class BucketCounts(RootModel[dict[PublicBucket, int]]):
    """Per-bucket reciter tally of ``GET /api/public/stats``.

    A ``Record<PublicBucket, int>`` — every public bucket key is present (count
    0 when empty), and the counts sum to the total public reciter count
    (primary_bucket is mutually exclusive at the reciter level). Mirrors
    ``services.reference.public_state.stats``.
    """

    root: dict[PublicBucket, int] = Field(default_factory=dict)
