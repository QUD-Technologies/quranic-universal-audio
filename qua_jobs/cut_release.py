#!/usr/bin/env python3
"""HF Job entrypoint: cut a global GitHub release (v2 track).

Reads the inspector DB (read-only) to discover every recitation eligible for
GH releases (a current ``per_recitation_releases(track='ts')`` row), builds the
per-recitation tier
files + ``catalog.json`` + zip + content_hash, builds the dataset-level
``manifest.json`` + ``CHANGELOG.md``, computes the version, and uses the
GitHub REST API to create the release tag and upload every asset.

On success, POSTs the completion webhook with the per-recitation membership
payload; Inspector's ``services.admin.jobs.cut_release.complete()`` inserts
the ``gh_releases`` row + N ``gh_release_recitations`` rows and fires the
public ``released`` event.

The HF Job NEVER writes the inspector DB. Reads only.

Env:
  INSPECTOR_BUCKET_MOUNT    bucket mount root (default ``/data``)
  INSPECTOR_CODE_DIR        staged-code root (default: the script's repo root,
                            i.e. ``/aux/code`` in the Job); set for local sim
  RELEASE_VERSION           (optional) operator-supplied vX.Y.Z; bypasses auto-bump
  LAUNCHED_BY               (optional) hf_user_id of the operator
  JOB_ID                    HF-injected job id
  HF_TOKEN                  HF auth (for repo_config lookup)
  GH_RELEASE_TOKEN          fine-grained GH app token (scoped to releases on the public repo)
  INSPECTOR_WEBHOOK_URL     completion endpoint
  INSPECTOR_WEBHOOK_SECRET  HMAC shared secret
"""

from __future__ import annotations

import concurrent.futures
import datetime
import gzip
import hashlib
import importlib.resources
import io
import json
import logging
import multiprocessing
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_shared.digital_khatt import (  # noqa: E402
    DIGITAL_KHATT_FONT_FILENAME,
    DIGITAL_KHATT_SCRIPT_FILENAME,
    DIGITAL_KHATT_SCRIPT_ID,
    UNICODE_INDEXING,
)
from qua_shared.riwayat import (  # noqa: E402
    DEFAULT_SDK_RIWAYAH,
    UnsupportedRiwayah,
    resolve_sdk_slug,
)
from qua_shared.schemas import (  # noqa: E402
    DigitalKhattDoc,
    FileDigest,
    LetterTimestampsDoc,
    ReleaseCatalog,
    ReleaseCatalogAudio,
    ReleaseCoverage,
    ReleaseEdition,  # noqa: E402
    ReleaseManifest,
    ReleaseManifestRecitation,
    ReleaseRecitationCatalog,
    VerseTimestampsDoc,
    WordTimestampsDoc,
)
from qua_shared.schemas.wire.release import SCHEMA_VERSION  # noqa: E402
from qua_shared.verse_layout import (  # noqa: E402
    PadParams,
    build_verse_layouts,
    load_canonical_verses,
    load_shard_occurrences,
    pad_params_from_env,
    reshape_canonical,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cut_release")


GH_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Bucket + DB I/O.
# ---------------------------------------------------------------------------


def _bucket_root() -> Path:
    return Path(os.environ.get("INSPECTOR_BUCKET_MOUNT", "/data"))


def _code_root() -> Path:
    """Staged-code root. In the HF Job this is ``/aux/code`` (aligner-bucket
    mounted RO), which equals ``_REPO_ROOT`` since the script lives at
    ``<root>/qua_jobs/cut_release.py``. ``INSPECTOR_CODE_DIR`` overrides it
    for local runs / the cut-sim harness; unset → the script's own repo root,
    so a plain local invocation reads ``data/``, ``LICENSE`` etc. from the
    checkout."""
    override = os.environ.get("INSPECTOR_CODE_DIR", "").strip()
    return Path(override) if override else _REPO_ROOT


def _open_inspector_db_readonly() -> sqlite3.Connection:
    """Open the bucket's inspector.db read-only. The Inspector is the single
    writer; this reader takes no locks and is safe against concurrent writes.
    """
    db_path = _bucket_root() / "db" / "inspector.db"
    if not db_path.exists():
        raise FileNotFoundError(f"inspector DB not found at {db_path}")
    uri = f"file:{db_path}?mode=ro&immutable=0"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _eligible_recitations(conn: sqlite3.Connection) -> list[dict]:
    """Return ``[{slug, ts_version, delivery_meta, channel_meta, reciter_meta}, ...]``
    for every recitation eligible for GH release: a current
    ``per_recitation_releases(track='ts')`` row. EveryAyah is excluded from
    this public projection even when it has a timestamp row.

    Selects both the FK slugs (``riwayah``/``style``/``channel`` — kept so
    ``catalog.json`` keeps its stable consumer schema) AND the vocab display names
    (``*_name``) used by the human-facing changelog.
    """
    rows = conn.execute("""
        SELECT
          prr.id AS prr_id,
          prr.slug AS slug,
          prr.version AS ts_version,
          prr.produced_at AS ts_produced_at,
          d.reciter_id AS reciter_id,
          d.riwayah AS riwayah,
          d.style AS style,
          d.channel AS channel,
          rw.name AS riwayah_name,
          st.name AS style_name,
          ch.name AS channel_name,
          d.audio_category AS audio_category,
          d.recording_context AS recording_context,
          d.recording_year AS recording_year,
          d.variant_label AS variant_label,
          d.chapter_count AS chapter_count,
          d.bitrate_mode AS bitrate_mode,
          d.bitrate_kbps_nominal AS bitrate_kbps_nominal,
          d.sample_rate_hz AS sample_rate_hz,
          d.channels AS channels,
          r.name_en AS name_en,
          r.name_ar AS name_ar,
          r.country AS country
        FROM per_recitation_releases prr
        JOIN deliveries d ON d.slug = prr.slug
        JOIN riwayahs rw  ON rw.slug = d.riwayah
        JOIN styles st    ON st.slug = d.style
        JOIN channels ch  ON ch.slug = d.channel
        JOIN reciters r   ON r.reciter_id = d.reciter_id
        JOIN delivery_states ds ON ds.slug = d.slug
        WHERE prr.track = 'ts'
          AND prr.superseded_at IS NULL
          AND ds.state = 'released'
          AND ds.visibility = 'public'
          AND d.channel <> 'everyayah'
        ORDER BY prr.slug
    """).fetchall()
    return [dict(r) for r in rows]


def _prior_release_members(conn: sqlite3.Connection) -> tuple[str | None, dict[str, dict]]:
    """Return ``(prior_version, {slug: member_row})`` for the most-recent
    non-superseded ``gh_releases``. Empty dict if there's no prior release.
    """
    rel = conn.execute(
        "SELECT id, version FROM gh_releases WHERE superseded_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not rel:
        return None, {}
    members = conn.execute(
        "SELECT * FROM gh_release_recitations WHERE release_id = ? ORDER BY slug",
        (rel["id"],),
    ).fetchall()
    return rel["version"], {m["slug"]: dict(m) for m in members}


# ---------------------------------------------------------------------------
# Tier-file projection — top-down (letter → word → verse).
# Every tier is an ordered occurrence timeline: every contiguous recited span
# is a row, in audio order, exactly one per verse flagged canonical; rows
# without changing the wire shape. Shared row prefixes are byte-equal.
# ---------------------------------------------------------------------------


def _load_occurrences(slug: str) -> list[dict]:
    """Every recited occurrence of every verse, in audio order."""
    return load_shard_occurrences(_bucket_root() / "reciters" / slug / "timestamps")


def _occurrence_key(ref: str, layout: dict) -> tuple[int, int, int, int]:
    """Timeline order: chapter, then start, then end, then mushaf ayah."""
    chapter, ayah = _verse_sort_key(ref)
    return chapter, int(layout["verse_start"]), int(layout["verse_end"]), ayah


def _load_canonical_verses(slug: str) -> dict[str, dict]:
    """Canonical verse map for ``slug`` (shared loader: project + dedup + merge)."""
    return load_canonical_verses(_bucket_root() / "reciters" / slug / "timestamps")


def _build_tier_files(
    slug: str,
    occurrences: list[dict],
    *,
    delivery_meta: dict,
    script_id: str,
    script_sha256: str,
    riwayah: str = DEFAULT_SDK_RIWAYAH,
    with_letters: bool = True,
) -> dict[str, bytes]:
    """Build the tier files (letter → word → verse, top-down projection).

    ``occurrences`` is ``[{"ref", "canonical", "layout"}, ...]`` — one entry per
    recited span, each ``layout`` a ``build_verse_layouts`` row. Rows are
    emitted in timeline order (chapter, then start); exactly one per ref must
    be canonical (``check_canonical_uniqueness`` runs before this).

    Returns ``{"verse_timestamps.json.gz": bytes,
               "word_timestamps.json.gz":  bytes,
               "letter_timestamps.json.gz": bytes}`` — the letter entry only
    when ``with_letters``. A proxy-timed (non-Hafs) delivery has no letter
    geometry at all, so the tier is absent rather than emitted empty: a consumer
    must be able to tell "this recitation has no letter timings" from "this
    verse happened to have none".

    All times are source-relative milliseconds. Occurrence ``start``/``end``
    are the audible bounds; ``silence_after`` is the gap until the next row in
    the same chapter timeline (0 for the chapter's last row). Every tier row is
    the exact prefix of the next tier's. HF clip padding remains an adapter
    concern and does not change this release timeline. The words/letters are
    the same psil-filtered, byte-exact alignment the dataset publishes.
    Positional arrays keep the public files compact; ``_meta`` describes layout.
    """
    ordered = sorted(
        (o for o in occurrences if o["layout"].get("words")),
        key=lambda o: _occurrence_key(o["ref"], o["layout"]),
    )

    verse_rows: list[list] = []
    word_rows: list[list] = []
    letter_rows: list[list] = []
    for index, occurrence in enumerate(ordered):
        ref, layout = occurrence["ref"], occurrence["layout"]
        # Release occurrence bound = last-audible timeline span. The HF adapter
        # separately uses the padded clip window from this same layout.
        start, end = int(layout["verse_start"]), int(layout["verse_end"])
        # Chapter boundaries are separate audio timelines unless/until source
        # duration is known, so a chapter's last row carries 0.
        silence_after = 0
        if index + 1 < len(ordered):
            nxt = ordered[index + 1]
            if _verse_sort_key(nxt["ref"])[0] == _verse_sort_key(ref)[0]:
                silence_after = max(0, int(nxt["layout"]["verse_start"]) - end)
        verse_row = [ref, start, end, bool(occurrence["canonical"]), silence_after]

        word_array = [[int(w[0]), int(w[1]), int(w[2])] for w in layout["words"]]
        token_array = [
            [
                int(token[0]),
                int(token[1]),
                int(token[2]),
                bool(token[3]),
                [[int(span[0]), int(span[1])] for span in token[4]],
            ]
            for token in layout["tokens"]
        ]
        verse_rows.append(verse_row)
        word_rows.append([*verse_row, word_array])
        letter_rows.append([*verse_row, word_array, layout["text"], token_array])

    meta_common = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "audio_category": delivery_meta.get("audio_category"),
        "units": "ms",
        "verse_count": len({row[0] for row in verse_rows}),
        "occurrence_count": len(verse_rows),
        "script": script_id,
        "script_sha256": script_sha256,
        "riwayah": riwayah,
        "unicode_indexing": UNICODE_INDEXING,
    }
    letter_doc = {
        "_meta": {
            **meta_common,
            "tier": "letter",
            "layout": "rows=[[ref,start,end,canonical,silence_after,words,text,tokens],...]; "
            "words=[[widx,start,end],...]; "
            "tokens=[[word_occurrence,start,end,owns_sound,paint],...]; "
            "paint=[[scalar_from,scalar_to],...]",
        },
        "rows": letter_rows,
    }
    word_doc = {
        "_meta": {
            **meta_common,
            "tier": "word",
            "layout": "rows=[[ref,start,end,canonical,silence_after,words],...]; "
            "words=[[widx,start,end],...]",
        },
        "rows": word_rows,
    }
    verse_doc = {
        "_meta": {
            **meta_common,
            "tier": "verse",
            "layout": "rows=[[ref,start,end,canonical,silence_after],...]",
        },
        "rows": verse_rows,
    }

    VerseTimestampsDoc.model_validate(verse_doc)
    WordTimestampsDoc.model_validate(word_doc)

    files = {
        "word_timestamps.json.gz": _gzip_deterministic(word_doc),
        "verse_timestamps.json.gz": _gzip_deterministic(verse_doc),
    }
    if with_letters:
        LetterTimestampsDoc.model_validate(letter_doc)
        files["letter_timestamps.json.gz"] = _gzip_deterministic(letter_doc)
    return files


def _release_occurrences(
    slug: str,
    verses: dict[str, dict],
    layouts: dict[str, dict],
    digital_khatt_words: dict,
    pads: PadParams,
) -> list[dict]:
    """``[{"ref", "canonical", "layout"}, ...]`` for the release timeline.

    The canonical take per verse is the gated ``verses`` map's layout (built as
    one batch so its clip pads see its neighbours). Every other recited span of
    a verse that survived the gate is laid out on its own — its pads are
    irrelevant to the public rows, which carry audible bounds. A verse whose
    canonical take was gated out contributes nothing: all its repeats are
    dropped with it, matching the published coverage semantics.
    """
    out = [
        {"ref": ref, "canonical": True, "layout": layout}
        for ref, layout in layouts.items()
        if not ref.startswith("_")
    ]
    for occurrence in _load_occurrences(slug):
        ref = occurrence["ref"]
        if occurrence["canonical"] or ref not in verses:
            continue
        layout = build_verse_layouts(
            reshape_canonical({ref: occurrence}, digital_khatt_words), **pads
        ).get(ref)
        if layout is not None:
            out.append({"ref": ref, "canonical": False, "layout": layout})
    return out


def _verse_for_validate(layout: dict) -> dict:
    """Project one shared verse layout to the boundary-validator's input shape.

    Validates the SAME invariants the HF dataset does, against the SAME byte-
    exact segments (gapless within a segment, gaps only across boundaries) — not
    the VAD segment windows the release used to validate against. Bounds are the
    padded clip window; all times source-relative ms.
    """
    cs = int(layout["clip_start"])
    ce = int(layout["clip_end"])
    return {
        "verse_start_ms": cs,
        "verse_end_ms": ce,
        "duration_ms": ce - cs,
        "words": [(int(w[0]), int(w[1]), int(w[2])) for w in layout["words"]],
        "segments": [(int(s[0]), int(s[1]), int(s[2]), int(s[3])) for s in layout["segments"]],
    }


def _verse_sort_key(key: str) -> tuple[int, int]:
    parts = key.split(":")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0, 0


def _gzip_deterministic(doc: dict) -> bytes:
    """Serialize JSON preserving insertion order + gzip at level 6, mtime=0."""
    payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(payload, compresslevel=6, mtime=0)


def _json_model_bytes(model) -> bytes:
    """Serialize a Pydantic model as deterministic compact JSON bytes."""
    body = model.model_dump(mode="json", by_alias=True)
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# Catalog + manifest + zip per recitation.
# ---------------------------------------------------------------------------


def _audio_sources_from_manifest(
    slug: str, audio_manifest: dict | None
) -> tuple[dict[str, str], dict[str, int]]:
    """Return ``(chapter_urls, chapter_offsets_ms)`` from
    ``catalog/audio_manifest/<slug>.json``.

    ``chapter_urls`` is the NATIVE source URL per chapter — for a combined-file
    intake (one YouTube/Drive source serving several chapters) the manifest
    stores a per-chapter bucket path in ``url`` and the real source in
    ``source_url``; we surface ``source_url`` so the release points consumers at
    the original, not the internal bucket. ``chapter_offsets_ms`` is the
    chapter's start offset *inside* that source (``source_offset_ms``), included
    only when > 0 (combined files, or a single file with a trimmed lead-in).
    Mirrors ``publish_hf.publish_slug``'s per-row resolution so the two adapters
    agree on provenance + offset.
    """
    if not audio_manifest:
        return {}, {}
    chapters = audio_manifest.get("chapters")
    if isinstance(chapters, dict):
        urls: dict[str, str] = {}
        offsets: dict[str, int] = {}
        for key, chapter in sorted(chapters.items()):
            if not (key.isdigit() or ":" in key) or not isinstance(chapter, dict):
                continue
            url = chapter.get("source_url") or chapter.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            urls[key] = url.strip()
            offset = int(chapter.get("source_offset_ms") or 0)
            if offset > 0:
                offsets[key] = offset
        if urls:
            return urls, offsets

    # Legacy flat maps are kept as a defensive fallback for old fixtures.
    flat = {
        key: value.strip()
        for key, value in sorted(audio_manifest.items())
        if (key.isdigit() or ":" in key) and isinstance(value, str) and value.strip()
    }
    return flat, {}


def _build_catalog_json(
    rec: dict,
    audio_manifest: dict | None,
    verses: dict,
    *,
    missing_surahs: str = "",
    missing_verses: str = "",
) -> bytes:
    """Per-recitation catalog.json bytes (orjson-equivalent serialisation).

    ``chapter_urls`` is keyed by chapter string (``"1"``) for by_surah and by
    ``"surah:ayah"`` for by_ayah — consumers interpret based on the recitation's
    ``audio_category``. Plan §"GH release `catalog.json` schema": "fully
    populated for every chapter the recitation covers" — both shapes are
    "what the source audio actually serves" so this is the consumer-actionable
    URL set without contraction.
    """
    audio_urls, audio_offsets = _audio_sources_from_manifest(rec["slug"], audio_manifest)
    if not audio_urls:
        raise RuntimeError(f"{rec['slug']}: audio_manifest has no usable audio URLs")
    surahs = {key.split(":", 1)[0] for key in verses if not key.startswith("_")}
    coverage_ayahs = sum(1 for k in verses if not k.startswith("_"))
    catalog = ReleaseRecitationCatalog(
        schema_version=SCHEMA_VERSION,
        slug=rec["slug"],
        reciter_id=rec.get("reciter_id"),
        name_en=rec.get("name_en"),
        name_ar=rec.get("name_ar"),
        riwayah=rec.get("riwayah"),
        style=rec.get("style"),
        country=rec.get("country"),
        channel=rec.get("channel"),
        audio_category=rec.get("audio_category"),
        recording_context=rec.get("recording_context"),
        recording_year=rec.get("recording_year"),
        variant_label=rec.get("variant_label"),
        audio=ReleaseCatalogAudio(
            chapter_urls=audio_urls,
            chapter_offsets_ms=audio_offsets,
            sample_rate_hz=rec.get("sample_rate_hz"),
            channels=rec.get("channels"),
            bitrate_mode=rec.get("bitrate_mode"),
            bitrate_kbps_nominal=rec.get("bitrate_kbps_nominal"),
        ),
        coverage=ReleaseCoverage(
            surahs=len(surahs),
            ayahs=coverage_ayahs,
            missing_surahs=missing_surahs,
            missing_verses=missing_verses,
        ),
    )
    return _json_model_bytes(catalog)


def _pack_recitation_zip(slug: str, files: dict[str, bytes]) -> bytes:
    """Pack the recitation's files (tier files + ``catalog.json``) into a
    deterministic zip.

    .gz entries: store (already compressed). .json/.md/.py: deflate level 9.
    mtime=0 on every entry header for byte stability.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", allowZip64=True) as zf:
        for name in sorted(files):
            data = files[name]
            method = zipfile.ZIP_STORED if name.endswith(".gz") else zipfile.ZIP_DEFLATED
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = method
            # Force deterministic external_attr (file mode 644) so the zip is
            # byte-stable across runs.
            info.external_attr = 0o644 << 16
            if method == zipfile.ZIP_DEFLATED:
                zf.writestr(info, data, compress_type=method, compresslevel=9)
            else:
                zf.writestr(info, data, compress_type=method)
    return buf.getvalue()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Dataset-level manifest + CHANGELOG.md + versioning.
# ---------------------------------------------------------------------------


def _classify_change_kind(rec: dict, prior_members: dict[str, dict], content_hash: str) -> str:
    """``added`` if no prior; ``unchanged`` if content_hash matches; else ``refresh``."""
    prior = prior_members.get(rec["slug"])
    if prior is None:
        return "added"
    if prior.get("content_hash") == content_hash:
        return "unchanged"
    return "refresh"


def _parse_version_override(override: str | None) -> str | None:
    """Normalise an operator ``RELEASE_VERSION`` to ``vX.Y.Z``; ``None`` when unset.

    Called at job start so a malformed override fails before the hour-long build.
    """
    if not override:
        return None
    parts = override.removeprefix("v").split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise RuntimeError(f"invalid release version {override!r}; expected vX.Y.Z")
    return f"v{'.'.join(parts)}"


def _compute_version(
    prior_version: str | None, members: list[dict], static_refs_changed: bool, override: str | None
) -> str:
    """Auto-bump from the prior version; an operator override wins as-is.
    No-op (every member 'unchanged' AND static refs unchanged) raises.
    """
    parsed = _parse_version_override(override)
    if parsed:
        return parsed
    if not prior_version:
        return "v0.1.0"
    parts = prior_version.lstrip("v").split(".")
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, IndexError) as e:
        raise RuntimeError(f"unparseable prior version {prior_version!r}") from e
    has_added = any(m["change_kind"] == "added" for m in members)
    has_refresh = any(m["change_kind"] == "refresh" for m in members)
    if has_added:
        return f"v{major}.{minor + 1}.0"
    if has_refresh or static_refs_changed:
        return f"v{major}.{minor}.{patch + 1}"
    raise RuntimeError("nothing changed since last release — set RELEASE_VERSION to force-cut")


def _build_dataset_manifest(
    version: str,
    prior_version: str | None,
    members: list[dict],
    static_refs: dict,
    editions: dict,
    owner: str,
    repo: str,
    created_at: str,
) -> bytes:
    """Dataset-level ``manifest.json``."""
    recitations: dict[str, ReleaseManifestRecitation] = {}
    for m in members:
        slug = m["slug"]
        recitations[slug] = ReleaseManifestRecitation(
            zip=f"{slug}.zip",
            zip_url=_release_asset_url(owner, repo, version, f"{slug}.zip"),
            sha256=m["zip_sha256"],
            bytes=m["zip_bytes"],
            coverage_ayahs=m["coverage_ayahs"],
            content_hash=m["content_hash"],
            change_kind=m["change_kind"],
            ts_version=m["ts_version"],
            tiers=m["tiers"],
            riwayah=m["shard_riwayah"],
        )
    manifest = ReleaseManifest(
        schema_version=SCHEMA_VERSION,
        release_version=version,
        created_at=created_at,
        previous_version=prior_version,
        recitation_count=len(members),
        static_refs={k: FileDigest.model_validate(v) for k, v in static_refs.items()},
        editions={k: ReleaseEdition.model_validate(v) for k, v in editions.items()},
        recitations=recitations,
        license="CC-BY-4.0",
    )
    return _json_model_bytes(manifest)


def _release_asset_url(owner: str, repo: str, version: str, name: str) -> str:
    return f"https://github.com/{owner}/{repo}/releases/download/{version}/{name}"


def _build_changelog(
    version: str,
    prior_version: str | None,
    members: list[dict],
    static_refs_changed_keys: list[str],
    owner: str,
    repo: str,
    created_at_date: str,
    hf_dataset: str,
) -> bytes:
    """The release body — delegates to the shared renderer so the modal preview and
    the shipped release stay byte-identical (modulo coverage, which the preview shows
    in surahs and the cut shows in exact ayahs). Maps the cut-side rich member dict
    onto the renderer's display-name contract."""
    from qua_shared.release_changelog import render_changelog

    render_members = [
        {
            "name_en": m.get("name_en"),
            "name_ar": m.get("name_ar"),
            "riwayah": m.get("riwayah_name") or m.get("riwayah"),
            "style": m.get("style_name") or m.get("style"),
            "channel": m.get("channel_name") or m.get("channel"),
            "change_kind": m.get("change_kind"),
            "coverage_surahs": m.get("coverage_surahs"),
            "coverage_ayahs": m.get("coverage_ayahs"),
            "missing_surahs": m.get("missing_surahs"),
            "missing_verses": m.get("missing_verses"),
            # Which timing depths this recitation actually ships. Dropping it
            # made every proxy-timed delivery advertise letter timings it has
            # not got, which is the whole point of the column.
            "tiers": m.get("tiers"),
        }
        for m in members
    ]

    md = render_changelog(
        version=version,
        previous_version=prior_version,
        release_date=created_at_date,
        members=render_members,
        static_refs_changed_keys=tuple(static_refs_changed_keys),
        owner=owner,
        repo=repo,
        hf_dataset=hf_dataset,
    )
    return md.encode("utf-8")


# ---------------------------------------------------------------------------
# GitHub REST API (stdlib only).
# ---------------------------------------------------------------------------


def _gh_request(
    method: str,
    path: str,
    token: str,
    *,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
    accept: str = "application/vnd.github+json",
) -> dict:
    """One GH REST call. Body is either JSON (json_body) or raw (raw_body).
    Returns the parsed JSON response on 2xx; raises with body text on failure.
    """
    url = path if path.startswith("http") else GH_API + path
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "quranic-universal-audio-cut-release",
    }
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    else:
        data = None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read()
            if not body:
                return {}
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"_raw": body.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        # On a 403, GitHub returns the permission the token lacks here — surface
        # it so an under-scoped GH_RELEASE_TOKEN is self-diagnosing instead of an
        # opaque "Resource not accessible by personal access token".
        needed = e.headers.get("X-Accepted-GitHub-Permissions")
        hint = f" [token needs GitHub permissions: {needed}]" if needed else ""
        raise RuntimeError(f"GH API {method} {url} → {e.code}: {msg[:500]}{hint}") from e


def _gh_release_exists(owner: str, repo: str, version: str, token: str) -> bool:
    """True when a release tagged ``version`` already exists on GitHub."""
    try:
        _gh_request("GET", f"/repos/{owner}/{repo}/releases/tags/{version}", token)
    except RuntimeError as exc:
        if "→ 404" in str(exc):
            return False
        raise
    return True


def _gh_create_release(owner: str, repo: str, version: str, body: str, token: str) -> dict:
    """Create a draft-less release tag. Returns the release dict (with upload_url)."""
    return _gh_request(
        "POST",
        f"/repos/{owner}/{repo}/releases",
        token,
        json_body={
            "tag_name": version,
            "name": version,
            "body": body,
            "draft": False,
            "prerelease": False,
        },
    )


def _gh_upload_asset(
    upload_url_template: str, name: str, data: bytes, token: str, content_type: str
) -> dict:
    """Upload one asset. upload_url_template ends with ``{?name,label}``."""
    base = upload_url_template.split("{", 1)[0]
    from urllib.parse import quote

    url = f"{base}?name={quote(name)}"
    return _gh_request(
        "POST",
        url,
        token,
        raw_body=data,
        content_type=content_type,
        accept="application/vnd.github+json",
    )


# ---------------------------------------------------------------------------
# Completion callback.
# ---------------------------------------------------------------------------


def _post_webhook(
    *,
    version: str,
    job_id: str,
    external_uri: str,
    members: list[dict],
    launched_by: str | None,
    status: str = "succeeded",
    validation_summary: dict | None = None,
) -> bool:
    url = os.environ.get("INSPECTOR_WEBHOOK_URL", "").strip()
    secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        log.info("webhook URL/secret unset — skipping callback (poll fallback applies)")
        return False
    body = {
        "kind": "cut_release",
        "job_id": job_id,
        "status": status,
        "version": version,
        "external_uri": external_uri,
        "launched_by": launched_by,
        "members": members,
    }
    if validation_summary:
        body["validation_summary"] = validation_summary
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Inspector-Job-Secret": secret},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            log.info("webhook %s → %s", url, resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("webhook POST failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# DigitalKhatt assets + static refs hash.
# ---------------------------------------------------------------------------
def _load_digital_khatt_assets(code_root: Path) -> tuple[bytes, bytes]:
    script = (code_root / "data" / DIGITAL_KHATT_SCRIPT_FILENAME).read_bytes()
    font = (
        code_root / "inspector" / "frontend" / "public" / "fonts" / DIGITAL_KHATT_FONT_FILENAME
    ).read_bytes()
    try:
        DigitalKhattDoc.model_validate(json.loads(script))
    except Exception as exc:
        raise RuntimeError(
            f"{DIGITAL_KHATT_SCRIPT_FILENAME} is not valid DigitalKhatt JSON"
        ) from exc
    if not font:
        raise RuntimeError(f"{DIGITAL_KHATT_FONT_FILENAME} is empty")
    return script, font


_STATIC_CONTENT_TYPES = {
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".gz": "application/gzip",
}


def _static_content_type(name: str) -> str:
    return _STATIC_CONTENT_TYPES.get(Path(name).suffix, "application/json")


def _hash_static_refs(refs_dir: Path, static_assets: dict[str, bytes]) -> dict[str, dict]:
    """SHA-256 + byte size for every public static reference.

    ``surah_info.json`` plus the script + font pairs: Digital Khatt for Hafs and
    the edition assets (:func:`_edition_assets`) for every other riwayah in the
    release. Non-Hafs *provenance* (index ids, projection digest) is its own
    manifest block (:func:`_release_editions`) because it is not a file digest.
    """
    out: dict[str, dict] = {}
    plain = refs_dir / "surah_info.json"
    if plain.exists():
        body = plain.read_bytes()
        out["surah_info.json"] = {"sha256": _sha256_hex(body), "bytes": len(body)}
    for name, body in static_assets.items():
        out[name] = {"sha256": _sha256_hex(body), "bytes": len(body)}
    return out


def _assert_riwayat_agree(slug: str, shard_riwayah: str, catalog_riwayah: str | None) -> None:
    """Refuse to cut a delivery whose shards and catalog row name different editions.

    The shards stay authoritative for what the release *contains* (see the cut
    loop), but a row that disagrees is not a labelling nit: the row is what the
    HF dataset config, the request form and the manifest's ``riwayah_name`` key
    on, so publishing would ship one edition's timings filed under another's
    name. A row that is simply absent is Hafs by construction and no evidence.
    """
    if not catalog_riwayah:
        return
    try:
        catalog_sdk = resolve_sdk_slug(catalog_riwayah)
    except UnsupportedRiwayah:
        raise ValueError(
            f"{slug}: catalog row names unsupported riwayah {catalog_riwayah!r}"
        ) from None
    if catalog_sdk != shard_riwayah:
        raise ValueError(
            f"{slug}: shards are {shard_riwayah!r} but the catalog row says "
            f"{catalog_riwayah!r} — the delivery is mislabelled, not releasable"
        )


def _qua_domain():
    """The edition package, or a loud failure naming what is missing.

    The wheel is optional in the job image — a build without the deploy key is
    Hafs-only. A cut that reached a non-Hafs delivery on such a build cannot
    proceed (guessing Hafs would publish one edition's text under another
    edition's coordinates), so name the cause instead of surfacing a bare
    ImportError traceback from halfway down the manifest builder.
    """
    try:
        import qua_domain
    except ImportError as exc:
        raise ValueError(
            "qua-domain is not installed in this job image — a non-Hafs "
            "delivery cannot be released from a Hafs-only build"
        ) from exc
    return qua_domain


def _non_hafs(editions: set[str]) -> list[str]:
    return sorted(r for r in editions if r != DEFAULT_SDK_RIWAYAH)


def _edition_words_asset(riwayah: str) -> str:
    return f"{riwayah}_words.json.gz"


def _edition_assets(editions: set[str]) -> dict[str, bytes]:
    """The script + font pair for every non-Hafs edition in this release.

    The counterpart of the Digital Khatt pair Hafs ships: ``<riwayah>_words.json.gz``
    is the edition's exact word text keyed by its own coordinates (the gzipped
    ``[{ref, text}]`` list whose canonical-JSON digest is ``words_sha256``) and
    ``<riwayah>.ttf`` the font it is typeset for. Both come byte-for-byte from
    the pinned ``qua_domain`` wheel, so a consumer can render the word tier
    without installing anything.
    """
    out: dict[str, bytes] = {}
    for riwayah in _non_hafs(editions):
        domain = _qua_domain()
        edition = domain.get_edition(riwayah)
        words = (
            importlib.resources.files("qua_domain.generated.editions")
            .joinpath(f"{riwayah}.words.json.gz")
            .read_bytes()
        )
        if not words:
            raise RuntimeError(f"{riwayah}: packaged word script is empty")
        out[_edition_words_asset(riwayah)] = words
        out[edition.font.filename] = domain.read_font_asset(riwayah)
    return out


def _release_editions(editions: set[str]) -> dict[str, dict]:
    """Provenance for every non-Hafs edition this release contains."""
    out: dict[str, dict] = {}
    for riwayah in _non_hafs(editions):
        domain = _qua_domain()
        edition = domain.get_edition(riwayah)
        projection = domain.load_edition_projection(riwayah, reference_riwayah=DEFAULT_SDK_RIWAYAH)
        out[riwayah] = {
            "edition_id": edition.edition_id,
            "words_sha256": edition.words_sha256,
            "script_asset_sha256": edition.script_sha256,
            "font_family": edition.font_family,
            "projection_sha256": projection.projection_sha256,
            "words_asset": _edition_words_asset(riwayah),
            "font_asset": edition.font.filename,
        }
    return out


def _edition_script(riwayah: str, digital_khatt_sha256: str) -> tuple[str, str]:
    """``(script_id, script_sha256)`` naming the script a delivery's text is in.

    Hafs keeps the Digital Khatt pair every existing release names. Another
    edition names its own index revision — the same digest the shard's
    ``words_sha256`` pins, so a reader can prove the two came from one revision.
    The manifest surfaces that digest as ``editions[<riwayah>].words_sha256``;
    the edition's own script-asset digest lives beside it under a different key
    precisely so nobody verifies a tier file against the wrong one.
    """
    if riwayah == DEFAULT_SDK_RIWAYAH:
        return DIGITAL_KHATT_SCRIPT_ID, digital_khatt_sha256

    edition = _qua_domain().get_edition(riwayah)
    return edition.edition_id, edition.words_sha256


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def _repo_owner_name() -> tuple[str, str, str]:
    """Resolve (owner, repo, hf_dataset_id) from config_loader."""
    from qua_shared.config_loader import repo_config

    cfg = repo_config()
    return cfg["repo_owner"], cfg["repo_name"], cfg["hf_dataset"]


def _preflight() -> int:
    """Verify env + bucket + staged code dir. Returns 0 on go, non-zero exit
    code on first failure (each code maps to one cause)."""
    if not os.environ.get("HF_TOKEN", "").strip():
        log.error("HF_TOKEN secret is required")
        return 10
    if not os.environ.get("GH_RELEASE_TOKEN", "").strip():
        log.error("GH_RELEASE_TOKEN secret is required")
        return 2
    bucket = _bucket_root()
    if not bucket.exists():
        log.error("bucket mount missing at %s", bucket)
        return 12
    db_path = bucket / "db" / "inspector.db"
    if not db_path.exists():
        log.error("inspector.db missing at %s", db_path)
        return 13
    code_dir = _code_root()
    for rel in (
        "data/surah_info.json",
        f"data/{DIGITAL_KHATT_SCRIPT_FILENAME}",
        f"inspector/frontend/public/fonts/{DIGITAL_KHATT_FONT_FILENAME}",
        ".github/config/repo.yml",
        "docs/templates/release_body.md",
        "LICENSE",
        "qua_jobs/shard.py",
        "qua_jobs/check_updates.py",
        "qua_jobs/download_audio.py",
    ):
        if not (code_dir / rel).exists():
            log.error("staged file missing: %s", code_dir / rel)
            return 14
    return 0


# ---------------------------------------------------------------------------
# Per-recitation build — one worker per reciter, fanned out over a process pool.
# ---------------------------------------------------------------------------

#: Parallel build workers; unset = ``DEFAULT_BUILD_WORKERS`` (capped by CPU count).
#: Set ``1`` to force serial. A reciter build peaks at several GB, so one worker
#: per vCPU OOM-kills the 8-vCPU/32 GB ``cpu-upgrade`` flavor.
BUILD_WORKERS_ENV = "CUT_BUILD_WORKERS"
DEFAULT_BUILD_WORKERS = 3


@dataclass(frozen=True)
class _BuildContext:
    """Read-only inputs every reciter build shares; inherited by forked workers."""

    surah_info: dict
    digital_khatt_words: dict
    script_sha256: str
    prior_members: dict[str, dict]
    pads: PadParams


class _FatalViolations(Exception):
    """A reciter failed the hard boundary invariants; the whole cut aborts."""

    def __init__(self, slug: str, fatal: list, summary: dict) -> None:
        super().__init__(f"{slug}: {len(fatal)} fatal boundary violations")
        self.slug, self.fatal, self.summary = slug, fatal, summary


#: Set once in the parent before the pool forks so workers inherit it without
#: pickling the multi-MB DigitalKhatt word index per task.
_BUILD_CTX: _BuildContext | None = None


def _build_workers() -> int:
    raw = os.environ.get(BUILD_WORKERS_ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, min(DEFAULT_BUILD_WORKERS, os.cpu_count() or 1))


def _build_members(eligible: list[dict], ctx: _BuildContext) -> list[dict]:
    """Build every eligible recitation, in catalog order, on a forked process
    pool (serial when forking is unavailable or a single worker is requested).
    Raises ``_FatalViolations`` for the first reciter that fails hard."""
    global _BUILD_CTX  # noqa: PLW0603 — fork-inherited context, see above
    _BUILD_CTX = ctx
    workers = min(_build_workers(), len(eligible))
    can_fork = "fork" in multiprocessing.get_all_start_methods()
    if workers <= 1 or not can_fork:
        results = [_build_member_task(rec) for rec in eligible]
    else:
        log.info("building %d recitations on %d workers", len(eligible), workers)
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("fork"),
        ) as pool:
            results = list(pool.map(_build_member_task, eligible))
    return [m for m in results if m is not None]


def _build_member_task(rec: dict) -> dict | None:
    assert _BUILD_CTX is not None
    return _build_member(rec, _BUILD_CTX)


def _verse_counts(riwayah: str, surah_info: dict) -> dict[int, int]:
    from qua_shared.coverage import verse_counts_from_surah_info
    from qua_shared.surah_words import surah_info_for

    return verse_counts_from_surah_info(surah_info_for(riwayah, surah_info))


def _validate_occurrences(slug: str, occurrences: list[dict], edition_counts: dict) -> dict:
    """Boundary-validate the SAME invariants the dataset does, against the
    byte-exact segments (gapless within a segment, gaps only across
    boundaries) — source-relative ms. Non-canonical takes are keyed
    ``ref#n`` so they skip the coverage check (a partial repeat is
    incomplete by definition) but still face the span invariants.
    Raises ``_FatalViolations`` on any hard failure."""
    from qua_shared.dataset_validation import (
        check_canonical_uniqueness,
        fatal_violations,
        validate_dataset,
    )

    for_validate: dict[str, dict] = {}
    for occurrence in occurrences:
        key = occurrence["ref"]
        if not occurrence["canonical"]:
            key = f"{key}#{sum(1 for k in for_validate if k.startswith(key + '#')) + 1}"
        for_validate[key] = _verse_for_validate(occurrence["layout"])
    rec_summary = validate_dataset(
        for_validate,
        expected_words={f"{s_num}:{a_num}": n for (s_num, a_num), n in edition_counts.items()},
    )
    uniqueness = check_canonical_uniqueness((o["ref"], o["canonical"]) for o in occurrences)
    rec_summary["violations"].extend(uniqueness)
    rec_summary["violation_count"] += len(uniqueness)
    for v in uniqueness:
        rec_summary["by_kind"][v["violation"]] = rec_summary["by_kind"].get(v["violation"], 0) + 1
    fatal = fatal_violations(rec_summary["violations"])
    if fatal:
        raise _FatalViolations(slug, fatal, rec_summary)
    return rec_summary


def _build_member(rec: dict, ctx: _BuildContext) -> dict | None:
    """One recitation's tier files + catalog.json + member row (``None`` when it
    has no shards). Pure function of the bucket + ``ctx``; safe in a worker."""
    from qua_shared.coverage import missing_coverage
    from qua_shared.surah_words import word_counts_for
    from qua_shared.timestamps_native import select_complete_verses

    slug = rec["slug"]
    log.info("  building %s...", slug)
    verses = _load_canonical_verses(slug)
    if not verses:
        log.warning("  %s: no timestamps shards — skipping", slug)
        return None

    # The shards say which edition and how deep their timings go; the
    # catalog row is not consulted here so a mislabelled row cannot make the
    # release claim letter timings a proxy-timed delivery does not have.
    shard_meta = verses.pop("_meta", {})
    with_letters = shard_meta.get("profile", "native") == "native"
    if not with_letters and not shard_meta.get("riwayah"):
        raise ValueError(f"{slug}: word-profile shards name no riwayah")
    riwayah = shard_meta.get("riwayah") or DEFAULT_SDK_RIWAYAH
    _assert_riwayat_agree(slug, riwayah, rec.get("riwayah"))
    tiers = ["verse", "word", "letter"] if with_letters else ["verse", "word"]

    # Gate incomplete verses: any verse missing a reference word index (never
    # recited) is dropped from the release — absent from the tier JSON and
    # excluded from coverage_ayahs. The editor/TS tab still shows them.
    edition_counts = word_counts_for(riwayah, ctx.surah_info)
    verses, dropped_incomplete = select_complete_verses(verses, edition_counts)
    if dropped_incomplete:
        log.info(
            "  %s: gated %d incomplete verse(s) (missing words): %s",
            slug,
            len(dropped_incomplete),
            dropped_incomplete,
        )

    # Shared geometry: audible bounds, HF clip windows, and byte-exact psil
    # segments are all derived once. Each adapter selects its public view of
    # the SAME layout, so timing/token ownership cannot drift.
    layouts = build_verse_layouts(reshape_canonical(verses, ctx.digital_khatt_words), **ctx.pads)
    occurrences = _release_occurrences(slug, verses, layouts, ctx.digital_khatt_words, ctx.pads)
    rec_summary = _validate_occurrences(slug, occurrences, edition_counts)

    # Tier files: every recited occurrence in timeline order, one canonical
    # per verse.
    script_id, edition_script_sha256 = _edition_script(riwayah, ctx.script_sha256)
    tier_files = _build_tier_files(
        slug,
        occurrences,
        delivery_meta=rec,
        script_id=script_id,
        script_sha256=edition_script_sha256,
        riwayah=riwayah,
        with_letters=with_letters,
    )

    # catalog.json.
    audio_manifest_path = _bucket_root() / "catalog" / "audio_manifest" / f"{slug}.json"
    audio_manifest = None
    if audio_manifest_path.exists():
        try:
            audio_manifest = json.loads(audio_manifest_path.read_bytes())
        except (json.JSONDecodeError, OSError):
            audio_manifest = None
    # Concise coverage-gap notation (vs the full mushaf) for catalog.json +
    # the changelog Missing column — whole missing surahs vs within-surah
    # verse gaps, split so even a partial recitation stays short.
    present_refs = {
        (int(k.split(":")[0]), int(k.split(":")[1])) for k in verses if not k.startswith("_")
    }
    missing_surahs, missing_verses = missing_coverage(
        present_refs, _verse_counts(riwayah, ctx.surah_info)
    )
    catalog_bytes = _build_catalog_json(
        rec,
        audio_manifest,
        verses,
        missing_surahs=missing_surahs,
        missing_verses=missing_verses,
    )

    # content_hash — over the DEEPEST emitted tier + catalog bytes. The
    # shallower tiers are exact prefixes of it, so hashing the deepest one
    # still detects any timing change; naming it by tier keeps the hash
    # meaningful for a delivery that has no letter tier.
    deepest = f"{tiers[-1]}_timestamps.json.gz"
    content_hash = _sha256_hex(tier_files[deepest] + catalog_bytes)

    files = dict(tier_files)
    files["catalog.json"] = catalog_bytes

    coverage_ayahs = sum(1 for k in verses if not k.startswith("_"))
    change_kind = _classify_change_kind(rec, ctx.prior_members, content_hash)

    return {
        "slug": slug,
        "name_en": rec.get("name_en"),
        "name_ar": rec.get("name_ar"),
        "riwayah": rec.get("riwayah"),
        "style": rec.get("style"),
        "channel": rec.get("channel"),
        "riwayah_name": rec.get("riwayah_name"),
        "style_name": rec.get("style_name"),
        "channel_name": rec.get("channel_name"),
        "ts_version": str(rec["ts_version"]),
        "tiers": tiers,
        "shard_riwayah": riwayah,
        "coverage_ayahs": coverage_ayahs,
        "coverage_surahs": rec.get("chapter_count"),
        "missing_surahs": missing_surahs,
        "missing_verses": missing_verses,
        "content_hash": content_hash,
        "change_kind": change_kind,
        # Frozen at cut time — what the ledger row stores.
        "catalog_snapshot": json.loads(catalog_bytes.decode("utf-8")),
        "_files": files,
        "_zip_bytes": None,  # filled after version is known
        "_validation": rec_summary,
        "zip_sha256": "",
        "zip_bytes": 0,
    }


def main() -> int:
    job_id = os.environ.get("JOB_ID", "").strip() or "unknown"
    launched_by = os.environ.get("LAUNCHED_BY") or None
    try:
        version_override = _parse_version_override(os.environ.get("RELEASE_VERSION", "").strip())
    except RuntimeError as exc:
        log.error("version override: %s", exc)
        return 6
    # Clip-edge knobs remain shared with HF because layout construction and
    # boundary validation use the same windows. Public GH rows expose the
    # audible occurrence span and following silence separately.
    pads = pad_params_from_env()

    rc = _preflight()
    if rc != 0:
        return rc
    gh_token = os.environ.get("GH_RELEASE_TOKEN", "").strip()

    owner, repo, hf_dataset = _repo_owner_name()
    log.info("cut_release: owner=%s repo=%s job=%s", owner, repo, job_id)

    # 1. Discover eligible recitations + prior release members.
    with _open_inspector_db_readonly() as conn:
        eligible = _eligible_recitations(conn)
        prior_version, prior_members = _prior_release_members(conn)
    log.info(
        "found %d eligible recitations; prior release: %s", len(eligible), prior_version or "<none>"
    )

    if not eligible:
        log.error("no eligible recitations — aborting")
        return 3

    # 2. Build per-recitation artifacts and accumulate member rows.
    refs_dir = _code_root() / "data"
    surah_info = json.loads((refs_dir / "surah_info.json").read_bytes())

    # The public projection is DigitalKhatt-only. Load and validate both assets
    # before reading any reciter so a broken staged image cannot make a release.
    try:
        digital_khatt_script, digital_khatt_font = _load_digital_khatt_assets(_code_root())
    except RuntimeError as exc:
        log.error("%s", exc)
        return 14
    digital_khatt_words = json.loads(digital_khatt_script)
    digital_khatt_assets = {
        DIGITAL_KHATT_SCRIPT_FILENAME: digital_khatt_script,
        DIGITAL_KHATT_FONT_FILENAME: digital_khatt_font,
    }
    script_sha256 = _sha256_hex(digital_khatt_script)

    now = datetime.datetime.now(datetime.UTC)
    created_at_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    created_at_date = now.strftime("%d-%m-%Y")

    validation_summary_total = {"violation_count": 0, "by_kind": {}, "violations": []}
    zip_bytes_by_slug: dict[str, bytes] = {}

    ctx = _BuildContext(
        surah_info=surah_info,
        digital_khatt_words=digital_khatt_words,
        script_sha256=script_sha256,
        prior_members=prior_members,
        pads=pads,
    )
    try:
        members = _build_members(eligible, ctx)
    except _FatalViolations as exc:
        log.error("  %s: %d fatal boundary violations — aborting cut", exc.slug, len(exc.fatal))
        for v in exc.fatal[:5]:
            log.error("    %s", v)
        _post_webhook(
            version=version_override or "",
            job_id=job_id,
            external_uri="",
            members=[],
            launched_by=launched_by,
            status="failed",
            validation_summary={"slug": exc.slug, "summary": exc.summary},
        )
        return 4
    for m in members:
        rec_summary = m.pop("_validation")
        validation_summary_total["violation_count"] += rec_summary["violation_count"]
        for k, c in rec_summary.get("by_kind", {}).items():
            validation_summary_total["by_kind"][k] = (
                validation_summary_total["by_kind"].get(k, 0) + c
            )
    # SDK slugs seen across this release's shards — drives the per-edition
    # entries in ``static_refs``.
    release_editions: set[str] = {m["shard_riwayah"] for m in members}

    if not members:
        log.error("no members built — aborting")
        return 5

    # 3. Static refs hashes.
    prior_static = {}
    # Pull prior static_refs from prior dataset manifest. Simpler approach:
    # compare hashes against the live HEAD on GH releases. Best-effort.
    # Non-Hafs editions ship their own script + font beside the Digital Khatt
    # pair; hashing them into static_refs makes an edition bump a release bump.
    edition_assets = _edition_assets(release_editions)
    static_refs = _hash_static_refs(refs_dir, {**digital_khatt_assets, **edition_assets})
    static_refs_changed_keys: list[str] = []
    if prior_version:
        try:
            prior_manifest_url = _release_asset_url(owner, repo, prior_version, "manifest.json")
            prior_manifest = _fetch_url_json(prior_manifest_url)
            prior_static = (prior_manifest or {}).get("static_refs", {}) or {}
        except Exception as exc:
            log.warning("could not fetch prior manifest: %s", exc)
    for name, meta in static_refs.items():
        prior_meta = prior_static.get(name) or {}
        if prior_meta.get("sha256") != meta["sha256"]:
            static_refs_changed_keys.append(name)

    # 4. Compute version.
    try:
        version = _compute_version(
            prior_version, members, bool(static_refs_changed_keys), version_override
        )
    except RuntimeError as exc:
        log.error("version compute: %s", exc)
        _post_webhook(
            version="",
            job_id=job_id,
            external_uri="",
            members=[],
            launched_by=launched_by,
            status="failed",
        )
        return 6
    log.info("computed version: %s", version)

    # 5. Pack each recitation's zip (tier files + catalog.json — the zip
    # content is version-independent, so a single pass suffices).
    for m in members:
        zip_data = _pack_recitation_zip(m["slug"], m["_files"])
        m["_zip_bytes"] = zip_data
        m["zip_sha256"] = _sha256_hex(zip_data)
        m["zip_bytes"] = len(zip_data)
        zip_bytes_by_slug[m["slug"]] = zip_data

    # 6. Dataset-level manifest + CHANGELOG.
    dataset_manifest = _build_dataset_manifest(
        version,
        prior_version,
        members,
        {k: v for k, v in static_refs.items()},
        _release_editions(release_editions),
        owner,
        repo,
        created_at_iso,
    )
    changelog_md = _build_changelog(
        version,
        prior_version,
        members,
        static_refs_changed_keys,
        owner,
        repo,
        created_at_date,
        hf_dataset,
    )

    # 7. Read license + helpers for upload. DigitalKhatt assets were validated
    # before reciter projection and are uploaded byte-for-byte.
    license_path = _code_root() / "LICENSE"
    license_bytes = license_path.read_bytes() if license_path.exists() else b""
    shard_py = (_code_root() / "qua_jobs" / "shard.py").read_bytes()
    check_updates_py = (_code_root() / "qua_jobs" / "check_updates.py").read_bytes()
    download_audio_py = (_code_root() / "qua_jobs" / "download_audio.py").read_bytes()
    static_files: dict[str, bytes] = {**digital_khatt_assets, **edition_assets}
    si_path = refs_dir / "surah_info.json"
    if si_path.exists():
        static_files["surah_info.json"] = si_path.read_bytes()

    # 8. Create the GH release + upload all assets. A tag that already exists
    # means the ledger missed a prior cut (e.g. its webhook failed) — refuse
    # rather than bump from a stale prior version.
    if _gh_release_exists(owner, repo, version, gh_token):
        log.error(
            "release %s already exists on %s/%s — the Inspector ledger is behind GitHub; "
            "record it with `admin_release.py complete %s --job-id <job>` and re-cut",
            version,
            owner,
            repo,
            version,
        )
        _post_webhook(
            version=version,
            job_id=job_id,
            external_uri="",
            members=[],
            launched_by=launched_by,
            status="failed",
        )
        return 15
    log.info("creating GH release %s on %s/%s ...", version, owner, repo)
    rel = _gh_create_release(owner, repo, version, changelog_md.decode("utf-8"), token=gh_token)
    upload_url = rel["upload_url"]
    release_html_url = rel.get("html_url", "")

    # Upload order: small assets first, then zips.
    uploads: list[tuple[str, bytes, str]] = []
    uploads.append(("manifest.json", dataset_manifest, "application/json"))
    uploads.append(("CHANGELOG.md", changelog_md, "text/markdown"))
    if license_bytes:
        uploads.append(("LICENSE", license_bytes, "text/plain"))
    catalog_all = _build_dataset_level_catalog(members)
    uploads.append(("catalog.json", catalog_all, "application/json"))
    uploads.append(("shard.py", shard_py, "text/x-python"))
    uploads.append(("check_updates.py", check_updates_py, "text/x-python"))
    uploads.append(("download_audio.py", download_audio_py, "text/x-python"))
    for name, body in static_files.items():
        uploads.append((name, body, _static_content_type(name)))
    for m in members:
        uploads.append((f"{m['slug']}.zip", m["_zip_bytes"], "application/zip"))

    for name, data, ctype in uploads:
        log.info("  upload %s (%d bytes)...", name, len(data))
        _gh_upload_asset(upload_url, name, data, gh_token, ctype)

    # 9. Build the members payload for the webhook (drop heavy fields).
    webhook_members = [
        {
            "slug": m["slug"],
            "catalog_snapshot": m["catalog_snapshot"],
            "zip_sha256": m["zip_sha256"],
            "zip_bytes": m["zip_bytes"],
            "coverage_ayahs": m["coverage_ayahs"],
            "content_hash": m["content_hash"],
            "ts_version": m["ts_version"],
            "change_kind": m["change_kind"],
        }
        for m in members
    ]

    recorded = _post_webhook(
        version=version,
        job_id=job_id,
        external_uri=release_html_url,
        members=webhook_members,
        launched_by=launched_by,
        validation_summary={
            "violation_count": validation_summary_total["violation_count"],
            "by_kind": validation_summary_total["by_kind"],
        },
    )

    log.info("cut_release: done version=%s recitations=%d", version, len(members))
    if not recorded and os.environ.get("INSPECTOR_WEBHOOK_URL", "").strip():
        # The release is live on GitHub but the Inspector ledger never heard of
        # it; fail the job loudly so the next preview doesn't re-propose this tag.
        log.error(
            "ledger NOT updated for %s — run `admin_release.py complete %s --job-id %s`",
            version,
            version,
            job_id,
        )
        return 16
    return 0


def _build_dataset_level_catalog(members: list[dict]) -> bytes:
    """Dataset-level ``catalog.json`` — array of every recitation's catalog row."""
    catalog = ReleaseCatalog(
        schema_version=SCHEMA_VERSION,
        recitations=[
            ReleaseRecitationCatalog.model_validate(m["catalog_snapshot"]) for m in members
        ],
    )
    return _json_model_bytes(catalog)


def _fetch_url_json(url: str) -> dict | None:
    """Best-effort fetch of a public URL as JSON."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        # Legitimate when there's no prior release (first cut) — but a real
        # network/JSON error should be loud enough to investigate later.
        log.warning("could not fetch %s: %s", url, exc)
        return None


if __name__ == "__main__":
    sys.exit(main())
