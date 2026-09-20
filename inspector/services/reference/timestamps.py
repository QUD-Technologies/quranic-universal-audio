"""Timestamp manifest + per-chapter shard server.

Bucket-only: manifest is composed from state (released reciters)
+ catalog (display + delivery metadata). Per-chapter audio URLs are not in
the manifest — the FE reads them from the canonical ``/api/audio/surahs``
endpoint. Per-chapter shards are read as raw Brotli from
``<bucket>/reciters/<slug>/timestamps/<chapter>.json.br`` on demand. The
bucket body is the compact v12 wire body, so serving is a byte
pass-through cached through a small per-process LRU so chapter scrubbing
within one reciter doesn't re-pay the bucket fetch.

``verse_bytes()`` serves one ayah of a chapter instead of the whole shard, for
clients that show a single verse and would otherwise pay a chapter to read it
(Al-Baqarah is ~1 MB Brotli; its median verse slice is ~6 KB). The chapter is
inflated and parsed once, every verse's slice is serialized in that pass, and
the parsed document is dropped — see ``_chapter_slices()``.

``invalidate()`` drops the cache for tests / future hot-reload.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

import brotli
import orjson

from config import DK_SCRIPT_PATH
from qua_shared.catalog_visibility import is_everyayah_channel
from qua_shared.riwayat import (
    DEFAULT_RIWAYAH,
    DEFAULT_SDK_RIWAYAH,
    UnsupportedRiwayah,
    from_sdk_slug,
    resolve_sdk_slug,
)
from qua_shared.schemas import ReciterCatalog, TsManifestResponse
from qua_shared.timestamps_shards import MANIFEST_SCHEMA_VERSION
from services.audio.audio_meta import chapter_numbers, vbr_chapters_for_reciter
from services.reference.editions import EditionsUnavailable
from services.state import catalog as catalog_service
from services.state import state as state_service
from services.storage import data_dir, static_refs
from utils.formatting import slug_to_name

log = logging.getLogger("inspector")

# Resource keys served at /api/ts/resource/<key>. The OTF font is bundled
# with the SPA (frontend/public/fonts/) and intentionally absent here.
_RESOURCE_KEYS = ("qpc_hafs", "digital_khatt")

# Lazy-built caches. Single lock guards the build path; reads are dict
# lookups so we don't need to hold the lock past `_ensure_built()`.
_lock = threading.Lock()
_built = False
# The ``db_seq`` the cache was built at. The manifest is a projection of which
# slugs are ``released`` (+ catalog/sidecar metadata), so it MUST rebuild
# whenever the committed DB advances. Keying on ``db_seq`` (mirrors
# ``catalog.snapshot()``) makes the cache self-healing: any in-process state
# commit bumps ``db_seq``, so the next manifest request rebuilds — no reliance
# on the explicit post-commit ``invalidate()`` (which sits after the durable
# upload in ``state.transition`` and is skipped if that step raises). ``None``
# forces a rebuild (boot / explicit invalidate).
_built_seq: int | None = None
_manifest_bytes: bytes | None = None
_built_seq_by_visibility: dict[bool, int | None] = {False: None, True: None}
_manifest_bytes_by_visibility: dict[bool, bytes | None] = {False: None, True: None}
_resource_bytes: dict[str, bytes] = {}
# Slugs the manifest advertises (released + chapter-derivable). The shard route
# gates on this so a guessed ``/shard/<slug>/<ch>`` URL can't serve a
# non-released reciter's timestamps — the unified ``reciters/`` prefix no longer
# isolates WIP timestamps by folder, so the released invariant is enforced here.
_served_slugs: set[str] = set()
_served_slugs_by_visibility: dict[bool, set[str]] = {False: set(), True: set()}

_SHARD_LRU_CAP = 256
_shard_lru: OrderedDict[tuple[str, int], bytes] = OrderedDict()

# Per-chapter verse slices, keyed ``(reciter, chapter)``. The value holds every
# ayah's ready-to-send JSON body, so the expensive part (inflate + parse) is
# paid once per chapter and every later verse in it is a dict lookup.
#
# The cap is small on purpose: a chapter's slices together weigh about what its
# inflated shard does (Al-Baqarah, the largest, is ~7.8 MB), and a reader moves
# through one chapter at a time. Three lets a reader flip between chapters
# without re-parsing while bounding the cost at roughly one chapter's inflate.
_SLICE_LRU_CAP = 3
_slice_lru: OrderedDict[tuple[str, int], dict[int, bytes]] = OrderedDict()
# Compressed verse bodies, keyed ``(reciter, chapter, ayah)``. Brotli of one
# slice is ~1-2 ms, small enough to not need a cache for correctness, big enough
# to be worth skipping when a reader replays the same verse.
_VERSE_LRU_CAP = 256
_verse_lru: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()


def _build_manifest_dict(reciters_block: dict[str, dict]) -> dict:
    """Assemble + serialize the decompressed manifest body through the wire model.

    ``reciters_block`` carries one ``_bucket_reciter_block`` dict per advertised
    slug. The whole body round-trips through :class:`TsManifestResponse` so the
    shape stays in lockstep with the codegen'd FE type. Dumped WITHOUT
    ``exclude_none`` so a reciter's ``name_ar`` stays emitted as ``null`` when the
    catalog has no Arabic name (the FE distinguishes "no Arabic name" from
    "field absent").
    """
    manifest = TsManifestResponse.model_validate(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": "",
            "dataset_base_url": "",
            "shard_url_template": "/api/ts/shard/{reciter}/{chapter}",
            "verse_url_template": "/api/ts/verse/{reciter}/{chapter}",
            "resources": {key: f"/api/ts/resource/{key}" for key in _RESOURCE_KEYS},
            "reciters": reciters_block,
            "editions": _edition_blocks(reciters_block),
        }
    )
    return manifest.model_dump(mode="json", by_alias=True)


def _servable(slug: str | None) -> bool:
    """Can this deployment render *slug*'s script and coordinates?

    Hafs always: its script is inlined and its coordinates are the constants.
    Anything else needs the ``qua_domain`` wheel, which a Hafs-only image does
    not carry.
    """
    from services.reference import editions as editions_service

    if not slug:
        return True
    try:
        if resolve_sdk_slug(slug) == DEFAULT_SDK_RIWAYAH:
            return True
    except UnsupportedRiwayah:
        return False
    return editions_service.available()


def _edition_blocks(reciters_block: dict[str, dict]) -> dict[str, dict]:
    """Display assets for every non-Hafs edition an advertised reciter uses.

    Built from the reciters actually in the manifest rather than from the four
    supported slugs, so a Hafs-only deployment emits ``{}`` and the FE never
    fetches a 0.9 MB font it has no use for.

    A riwayah the runtime cannot serve is SKIPPED with a warning, not defaulted
    to Hafs: the FE treats an absent entry as "cannot render this edition",
    which surfaces as a clear failure instead of an edition rendered under the
    wrong script.
    """
    from services.reference import editions as editions_service

    blocks: dict[str, dict] = {}
    for block in reciters_block.values():
        slug = block.get("riwayah")
        if not slug:
            continue
        try:
            # Accepts either vocabulary: the field is a plain catalog string and
            # older rows / fixtures can carry the short SDK form. The dedupe
            # below has to happen on the canonical slug for the same reason —
            # ``blocks`` is keyed canonically, so a row holding ``warsh`` and a
            # row holding ``warsh_an_nafi`` are one edition, not two.
            sdk_slug = resolve_sdk_slug(slug)
            if sdk_slug == DEFAULT_SDK_RIWAYAH:
                continue
            # Metadata only — it is lru-cached and touches no file. Reading the
            # font bytes or hashing the refs payload here put a multi-MB cost on
            # every manifest build for digests nothing reads.
            metadata = editions_service.metadata(sdk_slug)
        except (UnsupportedRiwayah, EditionsUnavailable) as exc:
            log.warning("ts manifest: riwayah %s cannot be served (%s)", slug, exc)
            continue
        # The catalog column is free text and older rows hold the short SDK
        # form, but both routes below parse strictly — so the URLs are built
        # from the canonical Inspector slug, not from whatever the row said.
        key = from_sdk_slug(sdk_slug)
        blocks[key] = {
            "riwayah": sdk_slug,
            "edition_id": metadata.edition_id,
            "words_sha256": metadata.words_sha256,
            "font_url": f"/api/static/edition/{key}/font",
            "font_family": metadata.font_family,
            "refs_url": f"/api/static/quran-refs.json?riwayah={key}",
        }
    return blocks


def _build_resource_bytes() -> dict[str, bytes]:
    """Gzip every advertised resource for ``/api/ts/resource/<key>``.

    ``qpc_hafs`` resolves via ``static_refs.load_qpc_bytes`` (local image →
    bucket — the deployed Space's image ``.gz`` is an LFS pointer, so real
    bytes come from the bucket). ``digital_khatt`` ships uncompressed in the
    image and reads directly (HF auto-LFS is keyed on extension, so the
    ~10 MB ``*.json`` is exempt). A missing/unavailable resource is skipped,
    never raised — the manifest must not 500 on a degraded resource."""
    out: dict[str, bytes] = {}
    qpc = static_refs.load_qpc_bytes()
    if qpc:
        out["qpc_hafs"] = gzip.compress(qpc, compresslevel=6, mtime=0)
    if DK_SCRIPT_PATH.exists():
        out["digital_khatt"] = gzip.compress(DK_SCRIPT_PATH.read_bytes(), compresslevel=6, mtime=0)
    return out


def _published_reciter_slugs(*, include_everyayah: bool = False) -> list[str]:
    """Return slugs of reciters in the ``released`` lifecycle state.

    State alone — no bucket I/O. The lifecycle gate
    ``under_review → released`` (publish fires only on timestamps-job success)
    is what guarantees these slugs have timestamps published; we don't re-verify
    by walking the bucket dir.
    """
    catalog = catalog_service.snapshot()
    delivery_by_slug = {d.slug: d for d in catalog.deliveries}
    return [
        row.slug
        for row in state_service.all_rows()
        if row.state.value == "released"
        and row.visibility.value == "public"
        and (
            include_everyayah
            or not is_everyayah_channel(getattr(delivery_by_slug.get(row.slug), "channel", None))
        )
    ]


def _ts_chapters_for(slug: str, delivery) -> list[int]:
    """Resolve the chapter list the manifest advertises for ``slug``.

    For a fully-documented complete by_surah mushaf (``delivery.chapter_count ==
    114``) derive ``1..114`` from the catalog (DB) — robust to a sidecar whose
    chapter keys are partial/corrupt, and provably contiguous so no advertised
    chapter can 404 (the released gate guarantees all 114 shards exist).
    Everything else (partial reciters, by_ayah, no delivery) falls back to the
    gap-accurate sidecar keys.

    NOTE: ``vbr_chapters`` still comes from the sidecar (see
    ``_bucket_reciter_block``), so this hardens the chapter list but does not yet
    avoid the per-slug sidecar read — moving that field into the catalog is the
    follow-up that would let the manifest build skip the bucket entirely.
    """
    if (
        delivery is not None
        and getattr(delivery.audio_category, "value", delivery.audio_category) == "by_surah"
        and delivery.chapter_count == 114
    ):
        return list(range(1, 115))
    return chapter_numbers(slug)


def _bucket_reciter_block(
    slug: str,
    ts_chapters: list[int],
    catalog: ReciterCatalog,
    delivery=None,
) -> dict | None:
    """Compose a manifest reciter block for a bucket-mode reciter.

    Joins the catalog (display + delivery metadata) with the precomputed VBR
    chapter list. Per-chapter audio URLs are NOT carried here — the FE resolves
    them from the canonical ``/api/audio/surahs`` endpoint (audio-manifest
    sidecar), so a non-templatable source (e.g. per-chapter YouTube IDs) is
    served correctly. Falls back to slug-derived defaults when the catalog has
    no delivery for ``slug``.

    The caller passes one shared ``catalog`` snapshot so the manifest build
    doesn't re-snapshot (deep-copy) per reciter inside ``_ensure_built``, and
    the pre-resolved ``delivery`` so we don't re-``find_delivery`` it here.
    """
    if delivery is None:
        delivery = catalog.find_delivery(slug)
    reciter = catalog.find_reciter(delivery.reciter_id) if delivery is not None else None

    name_en = reciter.name_en if reciter is not None else slug_to_name(slug)
    name_ar = reciter.name_ar if reciter is not None else None
    riwayah = delivery.riwayah if delivery is not None else DEFAULT_RIWAYAH
    style = delivery.style if delivery is not None else "murattal"
    source = delivery.source if delivery is not None else ""
    recording_year = delivery.recording_year if delivery is not None else None
    audio_category = delivery.audio_category.value if delivery is not None else "by_surah"

    return {
        "name_en": name_en,
        "name_ar": name_ar,
        "riwayah": riwayah,
        "style": style,
        "source": source,
        # Tells apart two deliveries a reader would otherwise see as one name:
        # the same reciter, riwayah and style, recorded years apart.
        "recording_year": recording_year,
        "audio_category": audio_category,
        "ts_chapters": ts_chapters,
        "vbr_chapters": vbr_chapters_for_reciter(slug),
    }


def _ensure_built(*, include_everyayah: bool = False) -> None:
    """Lazy boot — build manifest from state + catalog + bucket listing.

    Idempotent and thread-safe. Shards are NOT eagerly loaded — see
    ``_load_bucket_shard()``.
    """
    global _built, _built_seq, _manifest_bytes, _served_slugs
    # Lazy import (keeps this module light, like catalog.snapshot()'s db import).
    from services import db as _db

    seq = _db.current_db_seq()
    if _built_seq_by_visibility[include_everyayah] == seq:
        return
    with _lock:
        # Re-check inside the lock; another thread may have just rebuilt at seq.
        if _built_seq_by_visibility[include_everyayah] == seq:
            return
        catalog = catalog_service.snapshot()
        reciters_block: dict[str, dict] = {}
        for slug in _published_reciter_slugs(include_everyayah=include_everyayah):
            delivery = catalog.find_delivery(slug)
            chapters = _ts_chapters_for(slug, delivery)
            if not chapters:
                # No catalog-derivable chapters AND no audio_manifest sidecar
                # (or unparseable keys) — skip rather than emit an empty chapter
                # list the FE can't render. Surfaces as "missing from dropdown".
                log.warning(
                    "timestamps: skipping %s — no chapter numbers derivable "
                    "from catalog or audio_manifest sidecar",
                    slug,
                )
                continue
            block = _bucket_reciter_block(slug, chapters, catalog, delivery)
            if block is None:
                continue
            if not _servable(block.get("riwayah")):
                # A build without ``qua_domain`` (no deploy key, or
                # INSPECTOR_MULTI_RIWAYAH=0) cannot serve this delivery's script
                # or coordinates, and advertising it anyway is worse than
                # omitting it: the font endpoint 503s, ``font-display: swap``
                # leaves the text in the DigitalKhatt fallback, and the reader
                # is shown one edition's words in another's typeface with no
                # indication anything is wrong. Omit is the documented
                # degradation.
                log.warning(
                    "timestamps: skipping %s — this build cannot serve riwayah %s",
                    slug,
                    block.get("riwayah"),
                )
                continue
            reciters_block[slug] = block

        served = set(reciters_block)
        manifest = _build_manifest_dict(reciters_block)
        body = gzip.compress(
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
            compresslevel=6,
            mtime=0,
        )
        _served_slugs_by_visibility[include_everyayah] = served
        _manifest_bytes_by_visibility[include_everyayah] = body
        _shard_lru.clear()
        _resource_bytes.clear()
        _resource_bytes.update(_build_resource_bytes())
        _built_seq_by_visibility[include_everyayah] = seq
        if not include_everyayah:
            # Preserve these names for existing diagnostics/tests and callers.
            _built = True
            _built_seq = seq
            _manifest_bytes = body
            _served_slugs = served
        log.info(
            "timestamps: built manifest (%d reciters, %d resources)",
            len(reciters_block),
            len(_resource_bytes),
        )


def _dev_fixture_shard(reciter: str, chapter: int) -> bytes | None:
    """Dev-only override: when ``TS_DEV_FIXTURES`` points at a bucket-shaped dir,
    serve ``<dir>/reciters/<reciter>/timestamps/<chapter>.json.br`` from disk
    instead of the bucket for local shard iteration. Never
    set in production."""
    base = os.environ.get("TS_DEV_FIXTURES")
    if not base:
        return None
    try:
        return (
            Path(base) / "reciters" / reciter / "timestamps" / f"{chapter}.json.br"
        ).read_bytes()
    except OSError:
        return None


def _load_bucket_shard(reciter: str, chapter: int) -> bytes | None:
    """Return the raw Brotli per-chapter shard from the bucket, or ``None``.

    The bucket body is the compact v13 wire body. The read path is a byte
    pass-through with no inflate/reshape/recompress. LRU
    so chapter scrubbing within one reciter doesn't re-pay the bucket fetch.
    """
    dev = _dev_fixture_shard(reciter, chapter)
    if dev is not None:
        return dev
    key = (reciter, chapter)
    cached = _shard_lru.get(key)
    if cached is not None:
        _shard_lru.move_to_end(key)
        return cached
    body = data_dir.read_timestamps_chapter_br(reciter, chapter)
    if body is None:
        return None
    _shard_lru[key] = body
    _shard_lru.move_to_end(key)
    while len(_shard_lru) > _SHARD_LRU_CAP:
        _shard_lru.popitem(last=False)
    return body


def manifest_bytes(*, include_everyayah: bool = False) -> bytes:
    _ensure_built(include_everyayah=include_everyayah)
    body = _manifest_bytes_by_visibility[include_everyayah]
    assert body is not None
    return body


def shard_bytes(
    reciter: str,
    chapter: int,
    allow_unreleased: bool = False,
    include_everyayah: bool = False,
) -> bytes | None:
    _ensure_built(include_everyayah=include_everyayah)
    row = state_service.get_row(reciter)
    if row is None or row.visibility.value != "public":
        return None
    delivery = catalog_service.find_delivery(reciter)
    if delivery is not None and is_everyayah_channel(delivery.channel) and not include_everyayah:
        return None
    # Only serve shards for reciters the manifest advertises (released + has
    # chapters). Folder-level isolation is gone post-unification, so enforce the
    # released gate here too — don't leak a non-released reciter's timestamps.
    # ``allow_unreleased`` is the owner-preview bypass: the route sets it when
    # the caller holds ``timestamps.view_unreleased`` (capability check lives in
    # the route — this service stays Flask-free), letting an owner read a
    # generated-but-unreleased reciter's shards.
    if reciter not in _served_slugs_by_visibility[include_everyayah] and not allow_unreleased:
        return None
    return _load_bucket_shard(reciter, chapter)


def _ayah_of(ref: str) -> int | None:
    """The ayah number in a ``"<chapter>:<ayah>"`` part ref, or ``None``.

    A reading's parts can also carry a non-verse ref (a basmala before the
    chapter, say). Those belong to no ayah and are skipped rather than guessed
    at, so a malformed ref can never be filed under verse 0.
    """
    _, _, tail = ref.partition(":")
    return int(tail) if tail.isdigit() else None


def _chapter_slices(reciter: str, chapter: int, body: bytes) -> dict[int, bytes] | None:
    """Every ayah of ``chapter`` as its own ready-to-send shard body.

    One inflate + one parse per chapter, then the parsed document is dropped:
    holding it would cost ~83 MB for Al-Baqarah against ~7.8 MB for its slices,
    and a client that reads verses never needs the chapter shape again.

    Each slice is a valid shard document — the same ``_meta`` and a subset of
    ``readings`` — so a client decodes a verse with the decoder it already has
    for chapters. It carries two extra keys: ``ayah`` (the one asked for) and
    ``ayahs`` (every ayah this chapter times), which is what a verse picker
    needs and cannot otherwise learn without downloading the chapter.

    A reading that spans several ayahs is kept whole in each of their slices.
    Splitting it would mean re-timing its cells, and the client already narrows
    a reading to the verse it asked for.
    """
    cached = _slice_lru.get((reciter, chapter))
    if cached is not None:
        _slice_lru.move_to_end((reciter, chapter))
        return cached

    try:
        doc = orjson.loads(brotli.decompress(body))
    except (brotli.error, orjson.JSONDecodeError) as exc:
        log.warning("timestamps: chapter %s/%s is not a readable shard: %s", reciter, chapter, exc)
        return None
    meta = doc.get("_meta") or {}
    readings = doc.get("readings") or []

    # ayah -> reading indices, in shard order and deduped (a reading with two
    # parts in the same ayah must still appear once in that ayah's slice).
    by_ayah: dict[int, dict[int, None]] = {}
    for index, reading in enumerate(readings):
        for part in reading.get("parts") or []:
            ref = part[0] if isinstance(part, list) else part.get("ref")
            ayah = _ayah_of(ref) if isinstance(ref, str) else None
            if ayah is not None:
                by_ayah.setdefault(ayah, {})[index] = None
    ayahs = sorted(by_ayah)

    slices = {
        ayah: orjson.dumps(
            {
                "_meta": meta,
                "ayah": ayah,
                "ayahs": ayahs,
                "readings": [readings[index] for index in by_ayah[ayah]],
            }
        )
        for ayah in ayahs
    }

    _slice_lru[(reciter, chapter)] = slices
    _slice_lru.move_to_end((reciter, chapter))
    while len(_slice_lru) > _SLICE_LRU_CAP:
        _slice_lru.popitem(last=False)
    return slices


def verse_bytes(
    reciter: str,
    chapter: int,
    ayah: int | None = None,
    allow_unreleased: bool = False,
    include_everyayah: bool = False,
) -> bytes | None:
    """Return one ayah of a chapter as Brotli shard bytes, or ``None``.

    ``ayah`` defaults to the chapter's first timed verse, so a client that has
    just switched chapters gets a verse and the chapter's ayah list in one
    request instead of asking what exists and then asking for it.

    Visibility is ``shard_bytes``'s — this serves a strict subset of the same
    document and must not widen who can read it.
    """
    body = shard_bytes(
        reciter,
        chapter,
        allow_unreleased=allow_unreleased,
        include_everyayah=include_everyayah,
    )
    if body is None:
        return None
    slices = _chapter_slices(reciter, chapter, body)
    if not slices:
        return None
    if ayah is None:
        ayah = next(iter(slices))
    raw = slices.get(ayah)
    if raw is None:
        return None

    key = (reciter, chapter, ayah)
    cached = _verse_lru.get(key)
    if cached is not None:
        _verse_lru.move_to_end(key)
        return cached
    # Quality 5 rather than the 6 a stored shard is built at: a verse is
    # compressed per request, and the last point costs more time than it saves
    # bytes on a body this small.
    compressed = brotli.compress(raw, quality=5, mode=brotli.MODE_TEXT)
    _verse_lru[key] = compressed
    _verse_lru.move_to_end(key)
    while len(_verse_lru) > _VERSE_LRU_CAP:
        _verse_lru.popitem(last=False)
    return compressed


def ts_validation_doc(
    reciter: str,
    allow_unreleased: bool = False,
    include_everyayah: bool = False,
) -> dict | None:
    """Verse-level ``ts_validation.json`` for a reciter, or ``None``.

    Same released/owner-preview gate as ``shard_bytes`` (capability check lives
    in the route). Returns ``None`` when the reciter isn't viewable or the
    sidecar is absent (reciter was never run with probe beams). Not cached:
    owner-preview-only traffic, and the file is re-written whenever a job
    re-runs — reading the small doc directly avoids a stale cache.
    """
    _ensure_built(include_everyayah=include_everyayah)
    row = state_service.get_row(reciter)
    if row is None or row.visibility.value != "public":
        return None
    delivery = catalog_service.find_delivery(reciter)
    if delivery is not None and is_everyayah_channel(delivery.channel) and not include_everyayah:
        return None
    if reciter not in _served_slugs_by_visibility[include_everyayah] and not allow_unreleased:
        return None  # not viewable → route returns 404
    # Viewable but never run with probe beams → empty doc (not a 404) so the
    # FE can render an empty panel.
    return data_dir.read_ts_validation_doc(reciter) or {"_meta": {}, "verses": {}}


def resource_bytes(name: str) -> bytes | None:
    _ensure_built()
    return _resource_bytes.get(name)


def invalidate() -> None:
    """Drop the cached manifest + shards.

    The ``db_seq`` check in ``_ensure_built`` is the authoritative rebuild
    trigger; this explicit drop is retained for tests and as a belt-and-braces
    hook (``state.transition`` still calls it post-commit). Also clears the
    audio-manifest sidecar caches the manifest build derives URLs from, so a
    re-extracted reciter's stale sidecar can't leak old URLs into the rebuild.
    """
    global _built, _built_seq, _manifest_bytes
    with _lock:
        _built = False
        _built_seq = None
        _manifest_bytes = None
        for visibility in (False, True):
            _built_seq_by_visibility[visibility] = None
            _manifest_bytes_by_visibility[visibility] = None
            _served_slugs_by_visibility[visibility].clear()
        _served_slugs.clear()
        _shard_lru.clear()
        _slice_lru.clear()
        _verse_lru.clear()
        _resource_bytes.clear()
    # Outside the lock — different module's cache, no ordering dependency.
    from services.storage import cache as _cache

    _cache.invalidate_audio_manifest_cache()
