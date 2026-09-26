#!/usr/bin/env python3
"""HF Job entrypoint: publish one recitation to the HF dataset (v2 track).

Reads the recitation's bucket artifacts (``detailed.json`` + per-chapter
``timestamps/<n>.json.br`` + Xing-master ``audio/<n>.mp3``) and pushes a
Parquet split to the public HF dataset under ``<riwayah>/<slug>``. Audio
clips are produced by in-process MP3 frame-index slicing
(``qua_shared/mp3_frames.py``) — each chapter is read + indexed once, then
every verse clip is a byte-exact frame-range copy with the start snapped to
the frame boundary <= clip_start; word timestamps re-base to that boundary.
No per-clip subprocess (no ffmpeg/ffprobe), no pydub decode/re-encode.

The HF Job NEVER writes ``db/inspector.db``. On success it POSTs the
completion webhook (``INSPECTOR_WEBHOOK_URL``) with the HF dataset revision
SHA in ``version``; Inspector's
``services.admin.jobs.hf_publish.complete()`` inserts the
``per_recitation_releases(track='hf', ...)`` row + fires the public
``released`` event. Webhook failure is tolerated — the 120 s poll worker
backstops via the same handler.

Env:
  SLUG                      (required) reciter slug
  INSPECTOR_BUCKET_MOUNT    bucket mount root (default ``/data``)
  JOB_ID                    HF-injected job id (forwarded in callback)
  HF_TOKEN                  HF auth (secret)
  INSPECTOR_WEBHOOK_URL     (optional) Inspector completion endpoint
  INSPECTOR_WEBHOOK_SECRET  (optional) HMAC shared secret
  LAUNCHED_BY               (optional) hf_user_id of the operator who clicked
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qua_shared.catalog_visibility import is_everyayah_channel  # noqa: E402
from qua_shared.inspector_notify import WEBHOOK_USER_AGENT  # noqa: E402
from qua_shared.mp3_frames import (  # noqa: E402
    FrameIndex,
    MultiFrameSlice,
    build_frame_index,
    slice_frames,
    slice_frames_multi,
)
from qua_shared.riwayat import (  # noqa: E402
    DEFAULT_RIWAYAH,
    DEFAULT_SDK_RIWAYAH,
    UnsupportedRiwayah,
    from_sdk_slug,
    resolve_sdk_slug,
)
from qua_shared.verse_layout import (  # noqa: E402
    build_verse_layouts,
    load_canonical_verses,
    pad_params_from_env,
    reshape_canonical,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("publish_hf")


# ---------------------------------------------------------------------------
# Static-ref loaders — DigitalKhatt + surah_info are staged alongside the code by
# ``base.stage_job_code`` and land at ``/aux/code/data/`` in the container.
# ---------------------------------------------------------------------------


def _seg_word_range(
    matched_ref: str, surah_num: str, ayah: int, surah_info: dict
) -> tuple[int, int] | None:
    """Word-index span (``word_from``, ``word_to``) a segment covers WITHIN one ayah.

    detailed.json segments carry no ``word_from``/``word_to`` — the span is
    encoded in ``matched_ref`` (``s:a:w-s:a:w`` or a single ``s:a:w``). Returns
    1-based ``(w_from, w_to)`` clipped to ``(surah_num, ayah)``, or ``None`` when
    the segment doesn't cover this ayah at all.
    """
    if not matched_ref:
        return None
    start, _, end = (
        matched_ref.partition("-") if "-" in matched_ref else (matched_ref, "", matched_ref)
    )
    sp = start.split(":")
    ep = end.split(":")
    if len(sp) != 3 or len(ep) != 3:
        return None
    try:
        s_su, s_ay, s_w = int(sp[0]), int(sp[1]), int(sp[2])
        e_su, e_ay, e_w = int(ep[0]), int(ep[1]), int(ep[2])
    except ValueError:
        return None
    su = int(surah_num)
    if (su, ayah) < (s_su, s_ay) or (su, ayah) > (e_su, e_ay):
        return None
    w_from = s_w if (s_su, s_ay) == (su, ayah) else 1
    if (e_su, e_ay) == (su, ayah):
        w_to = e_w
    else:
        verses = surah_info.get(str(su), {}).get("verses", [])
        w_to = verses[ayah - 1].get("num_words", e_w) if 0 <= ayah - 1 < len(verses) else e_w
    return w_from, max(w_from, w_to)


# ---------------------------------------------------------------------------
# Bucket I/O — direct path reads (bucket is mounted at /data in the job).
# ---------------------------------------------------------------------------


def _bucket_root() -> Path:
    return Path(os.environ.get("INSPECTOR_BUCKET_MOUNT", "/data"))


def _code_root() -> Path:
    return Path(os.environ.get("INSPECTOR_CODE_DIR", "/aux/code"))


def _load_detailed(slug: str) -> dict:
    """Read ``reciters/<slug>/detailed.json``. Raises if missing."""
    path = _bucket_root() / "reciters" / slug / "detailed.json"
    return json.loads(path.read_bytes())


def _load_audio_manifest(slug: str) -> dict | None:
    """Read ``catalog/audio_manifest/<slug>.json`` or None if absent."""
    path = _bucket_root() / "catalog" / "audio_manifest" / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_bytes())
    except (json.JSONDecodeError, OSError):
        return None


def _load_timestamps_shards(slug: str) -> dict[str, dict]:
    """Canonical verse map for ``slug`` (shared loader: project + dedup + merge)."""
    return load_canonical_verses(_bucket_root() / "reciters" / slug / "timestamps")


def _reshape_timestamps_for_rows(canonical: dict, digital_khatt_words: dict) -> dict[str, dict]:
    """Projection → ``build_verse_layouts`` input (shared with the GH release)."""
    return reshape_canonical(canonical, digital_khatt_words)


# ---------------------------------------------------------------------------
# Row construction.
# ---------------------------------------------------------------------------


def _detailed_by_ref(detailed: dict) -> dict[str, dict]:
    """Normalize detailed.json entries to a ``surah:ayah → entry`` dict."""
    out: dict[str, dict] = {}
    for entry in detailed.get("entries", []):
        ref = entry.get("ref")
        if ref is None:
            continue
        if ":" in str(ref):
            out[ref] = entry
            continue
        # by_surah: ref is chapter number — fan out via segments' matched_ref
        for seg in entry.get("segments", []):
            mref = seg.get("matched_ref", "")
            if not mref or "-" not in mref:
                continue
            sp = mref.split("-", 1)[0].split(":")
            ep = mref.split("-", 1)[1].split(":")
            try:
                s_surah = int(sp[0])
                s_ayah = int(sp[1])
                e_ayah = int(ep[1])
            except (ValueError, IndexError):
                continue
            for a in range(s_ayah, e_ayah + 1):
                vref = f"{s_surah}:{a}"
                out.setdefault(vref, entry)
    return out


def _i(x):
    """Coerce ms to int (round on float, pass-through on int)."""
    return int(round(x)) if isinstance(x, float) else int(x)


def _subtract_spans(lo: int, hi: int, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return ``[lo, hi]`` minus ``spans`` as a list of kept runs (source-ms).

    Used to excise interior no-match audio: ``spans`` are the no-match segments'
    time windows inside a verse, ``[lo, hi]`` the verse clip window. With no
    overlapping span the result is the single run ``[(lo, hi)]`` (the common,
    no-gap case → identical to today's single contiguous slice). Overlapping /
    out-of-window spans are clipped and merged.
    """
    clipped = sorted((max(lo, a), min(hi, b)) for a, b in spans if min(hi, b) > max(lo, a))
    runs: list[tuple[int, int]] = []
    cur = lo
    for a, b in clipped:
        if a > cur:
            runs.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        runs.append((cur, hi))
    return runs


def build_rows(
    timestamps: dict,
    detailed_by_ref: dict,
    surah_info: dict,
    chapter_urls: dict[str, str] | None = None,
    *,
    chapter_offsets: dict[str, int] | None = None,
    pad_start: int = 100,
    pad_end: int = 300,
    min_gap: int = 100,
) -> list[dict]:
    """Build dataset row metadata in canonical verse order.

    Each row has the same column shape as v1 + ``clip_start`` (source-ms
    boundary the audio slice starts at; consumed by the slicer + persisted
    as ``source_offset_ms``). ``chapter_urls`` maps ``str(chapter) -> source URL``
    (from the audio manifest) — detailed.json no longer carries a per-entry
    ``audio`` field, so ``source_url`` is resolved from here. ``chapter_offsets``
    maps ``str(chapter) -> source_offset_ms`` (the chapter's start inside its
    source file for combined-file intakes, else 0); it is added to each clip's
    in-chapter offset so the persisted ``source_offset_ms`` is absolute within
    the original source.
    """
    chapter_urls = chapter_urls or {}
    chapter_offsets = chapter_offsets or {}
    # Shared geometry: audible bounds, clip windows, byte-exact segments, words,
    # and animation tokens are all source-relative. HF publishes the padded
    # verse/segment/word view; GH publishes the occurrence + animation view.
    layouts = build_verse_layouts(
        timestamps,
        pad_start=pad_start,
        pad_end=pad_end,
        min_gap=min_gap,
        seg_word_range=lambda mref, sn, ay: _seg_word_range(mref, sn, ay, surah_info),
        detailed_by_ref=detailed_by_ref,
    )
    rows: list[dict] = []
    for surah_num in sorted(surah_info, key=int):
        surah = surah_info[surah_num]
        for verse_info in surah.get("verses", []):
            ayah = verse_info["verse"]
            ref = f"{surah_num}:{ayah}"
            entry = detailed_by_ref.get(ref)
            if not entry:
                continue
            layout = layouts.get(ref)
            if not layout:
                continue
            clip_start = layout["clip_start"]
            clip_end = layout["clip_end"]

            # Rebase the shared source-relative layout to clip-relative. The
            # byte-exact segment geometry (occurrence-pinned boundaries, outer
            # edges stretched to the clip) is owned by the shared builder.
            verse_segments = [
                [s[0], s[1], _i(s[2] - clip_start), _i(s[3] - clip_start)]
                for s in layout["segments"]
            ]
            verse_words = [
                [w[0], _i(w[1] - clip_start), _i(w[2] - clip_start)] for w in layout["words"]
            ]
            # Kept audio runs = the verse clip window minus any INTERIOR
            # no-match segment (empty matched_ref). A no-match segment stays in
            # detailed.json with its time window but contributes no ref/text/
            # words, so its audio would otherwise sit as a phantom gap inside the
            # single contiguous clip. Subtracting its span splits the clip into
            # runs the slicer stitches gaplessly. Leading/trailing no-match is
            # already outside [clip_start, clip_end] (the window spans first→last
            # kept word), so only interior gaps produce >1 run.
            nomatch_spans: list[tuple[int, int]] = []
            for seg in entry.get("segments", []) or []:
                if seg.get("matched_ref", ""):
                    continue  # has a ref → kept, not a no-match
                a = max(clip_start, int(seg.get("time_start", 0)))
                b = min(clip_end, int(seg.get("time_end", 0)))
                if b > a:
                    nomatch_spans.append((a, b))
            keep_runs = _subtract_spans(int(clip_start), int(clip_end), nomatch_spans)

            # source_url + chapter info for slicer.
            chapter = int(surah_num)
            rows.append(
                {
                    "surah": chapter,
                    "ayah": ayah,
                    "duration_ms": _i(clip_end - clip_start),
                    "text_uthmani": layout["text"],
                    "segments": verse_segments,
                    "word_timestamps": verse_words,
                    "source_url": chapter_urls.get(str(chapter), ""),
                    "chapter": chapter,
                    "clip_start": clip_start,
                    "clip_end": clip_end,
                    "keep_runs": keep_runs,
                    "source_offset_base_ms": chapter_offsets.get(str(chapter), 0),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# In-process frame-index audio slicing. The bucket holds Xing-injected MP3s
# per chapter at ``reciters/<slug>/audio/<ch>.mp3``. Each chapter is read once,
# its MP3 frame grid is parsed in pure Python, and every verse clip is a
# byte-exact copy of the frame range covering ``[clip_start, clip_end]`` — no
# per-clip ffmpeg/ffprobe subprocess. The start snaps back to the frame
# boundary <= clip_start; ``actual_start_ms`` is read off the grid, so
# word_timestamps rebase against the true frame boundary (the old ffprobe-snap
# heuristic mis-estimated this — see qua_shared/mp3_frames.py).
# ---------------------------------------------------------------------------


def _chapter_mp3_path(slug: str, chapter: int) -> Path:
    return _bucket_root() / "reciters" / slug / "audio" / f"{chapter}.mp3"


def _frame_slice(
    data: bytes, index: FrameIndex, start_ms: int, end_ms: int
) -> tuple[bytes, int, int] | None:
    """Frame-exact clip of ``[start_ms, end_ms]`` from an indexed chapter MP3.

    Returns ``(clip_bytes, actual_start_ms, actual_end_ms)`` — the byte range of
    the frames covering the window, with the start snapped to the frame boundary
    <= ``start_ms``. ``None`` for an empty/degenerate window (caller drops the
    verse). Mirrors ffmpeg ``-c copy``'s frame-copy semantics minus the
    subprocess; the produced bytes decode bit-identically to the ffmpeg slice's
    overlapping audio.
    """
    fs = slice_frames(data, index, start_ms, end_ms)
    if fs is None:
        return None
    return fs.data, fs.actual_start_ms, fs.actual_end_ms


def _slice_workers() -> int:
    """Worker count for the chapter-slicing pool.

    Slicing is **I/O-bound on the bucket mount** (each worker does one bulk
    multi-MB chapter read), NOT CPU-bound — the in-memory frame slicing is cheap
    pure-Python. The HF bucket *volume* mount thrashes under many concurrent
    large reads: 3 workers slice 6.2k rows / 114 chapters in ~17 s, but 9 (what
    quota+1 yields on cpu-upgrade's 8 vCPU) stalls so hard the loop didn't reach
    row 311 in 45 min. So this is capped LOW and decoupled from CPU count.
    ``INSPECTOR_SLICE_WORKERS`` overrides for tuning.
    """
    override = os.environ.get("INSPECTOR_SLICE_WORKERS", "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    quota: int | None = None
    # cgroup v2: ``<quota> <period>`` in microseconds ("max" = unbounded).
    try:
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if len(raw) == 2 and raw[0] != "max":
            quota = max(1, int(int(raw[0]) / int(raw[1])))
    except (OSError, ValueError):
        quota = None
    # cgroup v1 fallback.
    if quota is None:
        try:
            q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text().strip())
            p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text().strip())
            if q > 0 and p > 0:
                quota = max(1, int(q / p))
        except (OSError, ValueError):
            quota = None
    if quota is None:
        try:
            quota = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
        except AttributeError:
            quota = os.cpu_count() or 2
    # I/O-bound on the bucket mount → cap at 3 (proven fast) regardless of vCPU.
    return max(2, min(quota + 1, 3))


def _rebase_row(row: dict, actual_start_ms: int) -> None:
    """Re-base clip-relative word/segment times to the snapped boundary.

    ``actual_start_ms`` is the snapped source-ms the slice actually starts at
    (≤ ``row['clip_start']``). The delta is added to every clip-relative offset
    so widx i's start_ms remains correct relative to byte 0 of the audio clip.
    """
    requested = row["clip_start"]
    delta = requested - actual_start_ms  # >= 0; usually 0 or ~26 ms
    if delta == 0:
        return
    row["duration_ms"] = row["duration_ms"] + delta
    row["clip_start"] = actual_start_ms
    # clip_end is computed from clip_start + duration_ms so the invariant
    # holds after rebase. Leaving clip_end unchanged would silently de-sync
    # the row's audio-window arithmetic.
    row["clip_end"] = actual_start_ms + row["duration_ms"]
    row["word_timestamps"] = [[w[0], w[1] + delta, w[2] + delta] for w in row["word_timestamps"]]
    row["segments"] = [[s[0], s[1], s[2] + delta, s[3] + delta] for s in row["segments"]]


def _rebase_row_multi(row: dict, runs: list) -> None:
    """Re-base a stitched (gap-excised) clip's clip-relative times piecewise.

    Each ``RunMap`` in ``runs`` maps a source-ms window onto the concatenated
    clip timeline. A clip-relative time ``t`` (offset from the ORIGINAL
    ``clip_start``) is re-anchored by finding the run its source time
    ``t + clip_start`` falls in and mapping it to ``(t_src - run.actual_start_ms)
    + run.cum_offset_ms`` — so the gap audio between runs is removed and the
    surviving words/segments play back-to-back. Sets ``duration_ms`` to
    the stitched length and ``clip_start`` to the first run's snapped boundary
    (the source offset of the clip's byte 0).
    """
    orig_cs = row["clip_start"]

    def _map(t_rel: int) -> int:
        t_src = t_rel + orig_cs
        for r in runs:
            if r.actual_start_ms <= t_src <= r.actual_end_ms:
                return (t_src - r.actual_start_ms) + r.cum_offset_ms
        # Defensive: a time outside every run (e.g. landed in an excised gap or
        # past the end). Clamp to the nearest run boundary so the clip stays
        # monotonic rather than crashing — kept words never hit this in practice.
        if t_src <= runs[0].actual_start_ms:
            return 0
        for r in runs:
            if t_src < r.actual_start_ms:
                return r.cum_offset_ms
        last = runs[-1]
        return last.cum_offset_ms + (last.actual_end_ms - last.actual_start_ms)

    total = sum(r.actual_end_ms - r.actual_start_ms for r in runs)
    row["word_timestamps"] = [[w[0], _map(w[1]), _map(w[2])] for w in row["word_timestamps"]]
    row["segments"] = [[s[0], s[1], _map(s[2]), _map(s[3])] for s in row["segments"]]
    row["clip_start"] = runs[0].actual_start_ms
    row["duration_ms"] = total
    row["clip_end"] = runs[0].actual_start_ms + total


# ---------------------------------------------------------------------------
# Boundary validation — fatal violations abort; coverage gaps drop verses.
# ---------------------------------------------------------------------------


def _verses_for_validation(rows: list[dict]) -> dict[str, dict]:
    """Re-format rows as the ``validate_dataset`` input shape (source-ms).

    The validator operates on source-relative ms, so we re-anchor each row's
    clip-relative arrays back onto its ``clip_start`` to test the original
    upstream invariants (word-bleed, intra-segment gapless, coverage).
    """
    out: dict[str, dict] = {}
    for r in rows:
        ref = f"{r['surah']}:{r['ayah']}"
        cs = r["clip_start"]
        out[ref] = {
            "verse_start_ms": cs,
            "verse_end_ms": r["clip_end"],
            "duration_ms": r["duration_ms"],
            "words": [(w[0], w[1] + cs, w[2] + cs) for w in r["word_timestamps"]],
            "segments": [(s[0], s[1], s[2] + cs, s[3] + cs) for s in r["segments"]],
        }
    return out


# ---------------------------------------------------------------------------
# HF dataset push.
# ---------------------------------------------------------------------------


def _iter_hf_records(rows: list[dict], audio_bytes: list[bytes | None]):
    """Yield encoded dataset rows without building one multi-GB Arrow array."""
    for i, row in enumerate(rows):
        if audio_bytes[i] is None:
            continue
        src_url = row["source_url"]
        for prefix in ("https://", "http://"):
            if src_url.startswith(prefix):
                src_url = src_url[len(prefix) :]
                break
        yield {
            "audio": {
                "bytes": audio_bytes[i],
                "path": f"{row['surah']:03d}{row['ayah']:03d}.mp3",
            },
            "surah": row["surah"],
            "ayah": row["ayah"],
            "duration_ms": row["duration_ms"],
            "text_uthmani": row["text_uthmani"],
            "segments": row["segments"],
            "word_timestamps": row["word_timestamps"],
            "source_url": src_url,
            # Add the chapter's offset within its source file (combined-file
            # intakes) so the persisted offset is absolute within the original
            # source; 0 for normal chapters whose audio == the whole source.
            "source_offset_ms": _i(row["clip_start"] + row.get("source_offset_base_ms", 0)),
        }


def _push_to_hf(slug: str, riwayah: str, rows: list[dict], audio_bytes: list[bytes | None]) -> str:
    """Build the parquet split and push to HF. Returns the dataset commit SHA.

    ``riwayah`` is the INSPECTOR slug. It becomes ``config_name``, which is the
    dataset's top-level parquet folder — every published split already lives
    under ``hafs_an_asim/``, and switching vocabularies here would strand them.
    """
    from datasets import Audio, Dataset, Features, Sequence, Value
    from huggingface_hub import HfApi

    repo_id = _resolve_dataset_repo_id()
    api = HfApi(token=os.environ.get("HF_TOKEN"))
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)

    # Audio(decode=True) matches the existing splits on the hub (consumers
    # expect ``ds[i]["audio"]["array"]``). Torch + torchcodec are installed
    # on the job at launch (see services/admin/jobs/hf_publish.py).
    features = Features(
        {
            "audio": Audio(),
            "surah": Value("int32"),
            "ayah": Value("int32"),
            "duration_ms": Value("int32"),
            "text_uthmani": Value("string"),
            "segments": Sequence(Sequence(Value("int32"))),
            "word_timestamps": Sequence(Sequence(Value("int32"))),
            "source_url": Value("string"),
            "source_offset_ms": Value("int32"),
        }
    )
    # Dataset.from_dict constructs each column as one Arrow array. Embedded
    # audio for a full mushaf can exceed Arrow binary's 32-bit offset ceiling
    # before push_to_hub gets a chance to shard it. Build disk-backed record
    # batches instead so each binary array stays comfortably below 2 GiB.
    ds = Dataset.from_generator(
        _iter_hf_records,
        features=features,
        gen_kwargs={"rows": rows, "audio_bytes": audio_bytes},
        keep_in_memory=False,
        writer_batch_size=64,
        fingerprint=os.environ.get("JOB_ID") or slug,
    )

    log.info("pushing %d rows to %s/%s/%s", len(ds), repo_id, riwayah, slug)
    ds.push_to_hub(
        repo_id,
        config_name=riwayah,
        split=slug,
        token=os.environ.get("HF_TOKEN"),
        max_shard_size="500MB",
        commit_message=f"publish {riwayah}/{slug}",
    )
    # Resolve the dataset's HEAD commit sha — the version the row landed at.
    try:
        info = api.repo_info(repo_id=repo_id, repo_type="dataset")
        return getattr(info, "sha", "") or ""
    except Exception:
        return ""


def _resolve_dataset_repo_id() -> str:
    """Resolve the HF dataset repo id from config_loader (single source of truth)."""
    from qua_shared.config_loader import repo_config

    return repo_config()["hf_dataset"]


def _sync_dataset_catalog_and_card(repo_id: str) -> None:
    """Refresh the HF dataset catalog config and re-render the dataset card.

    The verse split was already pushed before this runs, so the published-split
    enumeration includes the just-published reciter. Frontmatter ``configs``, header
    badges, and the ``mushafs`` catalog stats are all derived from that one
    enumeration so they agree.
    """
    from qua_shared.config_loader import template_path
    from qua_shared.digital_khatt import (
        DIGITAL_KHATT_FONT_FILENAME,
        DIGITAL_KHATT_SCRIPT_FILENAME,
    )
    from qua_shared.hf_dataset_catalog import (
        hub_published_splits_by_config,
        push_catalog_dataset,
        render_dataset_card,
        sync_dataset_assets,
        upload_dataset_card,
    )

    db_path = _bucket_root() / "db" / "inspector.db"
    if not db_path.exists():
        raise RuntimeError(f"Inspector DB missing at {db_path}")
    token = os.environ.get("HF_TOKEN")
    # Stamp now for just-published rows that have no hf ledger row yet (the
    # ledger is written post-job by hf_publish.complete) so published_at /
    # updated_at aren't null in the catalog parquet.
    from datetime import UTC, datetime

    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    splits_by_config = hub_published_splits_by_config(repo_id=repo_id, token=token)
    published = {slug for slugs in splits_by_config.values() for slug in slugs}
    stats = push_catalog_dataset(
        repo_id=repo_id, db_path=db_path, token=token, published_slugs=published, now_iso=now_iso
    )
    card = render_dataset_card(
        template_path=template_path("hf_dataset_card"),
        splits_by_config=splits_by_config,
        stats=stats,
    )
    upload_dataset_card(repo_id=repo_id, content=card, token=token)
    code_root = _code_root()
    sync_dataset_assets(
        repo_id=repo_id,
        assets={
            DIGITAL_KHATT_SCRIPT_FILENAME: (
                code_root / "data" / DIGITAL_KHATT_SCRIPT_FILENAME
            ).read_bytes(),
            DIGITAL_KHATT_FONT_FILENAME: (
                code_root
                / "inspector"
                / "frontend"
                / "public"
                / "fonts"
                / DIGITAL_KHATT_FONT_FILENAME
            ).read_bytes(),
        },
        remove=("letter_vocab_hafs_qpc.csv", "qpc_hafs.json"),
        token=token,
    )


def _riwayah_for(audio_manifest: dict | None, detailed: dict) -> str:
    """Find the riwayah slug — it becomes the HF dataset config name.

    ``detailed.json``'s ``_meta.riwayah`` is the source in practice: it records
    the coordinate system ``matched_ref`` is expressed in, and it is what the
    promoter writes. The audio manifest's ``_meta.riwayah`` takes precedence
    when present, and the two must agree — but nothing writes it today, so that
    guard is a contract for a future manifest producer rather than a path this
    job currently takes.

    Falls back to Hafs only when neither source names a riwayah, which is every
    pre-multi-riwayah reciter.
    """
    manifest_riw = (audio_manifest or {}).get("_meta", {}).get("riwayah")
    detailed_riw = (detailed.get("_meta") or {}).get("riwayah")
    if manifest_riw and detailed_riw and manifest_riw != detailed_riw:
        raise ValueError(
            f"riwayah mismatch: audio manifest says {manifest_riw!r}, "
            f"detailed.json says {detailed_riw!r} — publishing would file the "
            f"rows under a config whose coordinates they are not in"
        )
    return manifest_riw or detailed_riw or DEFAULT_RIWAYAH


def _config_riwayah(audio_manifest: dict | None, detailed: dict) -> tuple[str, str]:
    """``(sdk slug, inspector slug)`` for a delivery about to be published.

    The Inspector slug is the HF **config name**, which is the dataset's
    top-level parquet folder: every split published so far lives under
    ``hafs_an_asim/<slug>-*``. Emitting the SDK slug there would open a second
    ``hafs/`` folder, duplicate the config in the card frontmatter and orphan
    the existing split. The SDK slug is what the shard `_meta` is compared
    against. Normalising through both spellings also collapses a legacy row
    that stored the short form onto the folder its edition already uses.
    """
    sdk = resolve_sdk_slug(_riwayah_for(audio_manifest, detailed))
    return sdk, from_sdk_slug(sdk)


# ---------------------------------------------------------------------------
# Completion callback.
# ---------------------------------------------------------------------------


def _post_webhook(
    *,
    slug: str,
    job_id: str,
    version: str,
    external_uri: str,
    status: str = "succeeded",
    validation_summary: dict | None = None,
) -> bool:
    """POST the completion webhook so Inspector commits the DB row. Returns
    True on 2xx; False on any failure (the 120s poll worker is the safety net)."""
    url = os.environ.get("INSPECTOR_WEBHOOK_URL", "").strip()
    secret = os.environ.get("INSPECTOR_WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        log.info("webhook URL/secret unset — skipping callback (poll fallback applies)")
        return False
    import urllib.request

    body = {
        "kind": "hf_publish",
        "slug": slug,
        "job_id": job_id,
        "status": status,
        "version": version,
        "external_uri": external_uri,
        "launched_by": os.environ.get("LAUNCHED_BY"),
    }
    if validation_summary:
        body["validation_summary"] = validation_summary
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Inspector-Job-Secret": secret,
            "User-Agent": WEBHOOK_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("webhook %s → %s", url, resp.status)
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("webhook POST failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Preflight — bail with clear exit codes before any heavy work.
# ---------------------------------------------------------------------------


def _preflight(slug: str) -> int:
    """Verify env, bucket inputs, static refs. Returns 0 on go,
    non-zero exit code on the first failure. Each return code maps to one
    cause so the operator can fix without diving into logs."""
    if not slug:
        log.error("SLUG env var is required")
        return 2
    if not os.environ.get("HF_TOKEN", "").strip():
        log.error("HF_TOKEN secret is required")
        return 10
    bucket = _bucket_root()
    if not bucket.exists():
        log.error("bucket mount missing at %s", bucket)
        return 12
    detailed_path = bucket / "reciters" / slug / "detailed.json"
    if not detailed_path.exists():
        log.error("detailed.json missing at %s", detailed_path)
        return 13
    refs_dir = _code_root() / "data"
    if not (refs_dir / "surah_info.json").exists():
        log.error("static ref surah_info.json missing at %s", refs_dir)
        return 14
    if not (refs_dir / "digital_khatt_v2_script.json").exists():
        log.error("static ref digital_khatt_v2_script.json missing at %s", refs_dir)
        return 14
    return 0


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def _result(
    slug: str,
    status: str,
    *,
    version: str = "",
    external_uri: str = "",
    validation_summary: dict | None = None,
    error: str | None = None,
    exit_code: int = 0,
) -> dict:
    """Build the ``publish_slug`` result dict (shared by single + batch)."""
    return {
        "slug": slug,
        "status": status,
        "version": version,
        "external_uri": external_uri,
        "validation_summary": validation_summary,
        "error": error,
        "exit_code": exit_code,
    }


def _is_everyayah_delivery(slug: str) -> bool:
    """Fail closed for the owner-only Inspector source at the public boundary."""
    db_path = _bucket_root() / "db" / "inspector.db"
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT channel FROM deliveries WHERE slug = ?", (slug,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return row is not None and is_everyayah_channel(row[0])


def publish_slug(
    slug: str,
    job_id: str,
    *,
    sync_card: bool = True,
    pad_start: int = 100,
    pad_end: int = 300,
    min_gap: int = 100,
) -> dict:
    """Publish one recitation to the HF dataset. Returns a result dict; never
    posts a webhook or raises (the caller — single or batch — owns reporting).

    ``sync_card`` re-renders the dataset catalog + card after the push. The
    single entrypoint runs it per publish; the batch runner skips it per slug
    and syncs once at the end (one push of N splits → one card render).

    Result: ``{slug, status: "succeeded"|"failed", version, external_uri,
    validation_summary, error, exit_code}``. ``exit_code`` lets the single
    entrypoint preserve its operator-facing process codes.
    """
    if _is_everyayah_delivery(slug):
        return _result(
            slug,
            "failed",
            error="EveryAyah deliveries are Inspector-owner-only and cannot be published to HF",
            exit_code=4,
        )
    rc = _preflight(slug)
    if rc != 0:
        return _result(slug, "failed", error=f"preflight failed (rc={rc})", exit_code=rc)

    # 1. Load bucket artifacts.
    detailed = _load_detailed(slug)
    audio_manifest = _load_audio_manifest(slug)
    canonical = _load_timestamps_shards(slug)
    if not canonical:
        log.error("no timestamps shards on bucket for %s", slug)
        return _result(slug, "failed", error="no timestamps shards on bucket", exit_code=3)
    # 2. Load the one public presentation and reference metadata.
    refs_dir = _code_root() / "data"
    surah_info = json.loads((refs_dir / "surah_info.json").read_bytes())
    digital_khatt_words = json.loads((refs_dir / "digital_khatt_v2_script.json").read_bytes())

    # The shards name the edition their coordinates are in; the delivery's own
    # records (the audio manifest, else detailed.json) must agree with them or
    # the rows would be filed under a config whose verse numbering they do not
    # follow.
    shard_riwayah = (canonical.pop("_meta", {}) or {}).get("riwayah", DEFAULT_SDK_RIWAYAH)
    # ``publish_slug`` owes its caller a result dict, never an exception — a
    # raise here leaves the Inspector's job row running forever with no webhook.
    try:
        delivery_riwayah, config_riwayah = _config_riwayah(audio_manifest, detailed)
    except (ValueError, UnsupportedRiwayah) as exc:
        log.error("riwayah unusable for %s: %s", slug, exc)
        return _result(slug, "failed", error=str(exc), exit_code=17)
    if delivery_riwayah != shard_riwayah:
        log.error(
            "riwayah mismatch: delivery says %s, shards are %s", config_riwayah, shard_riwayah
        )
        return _result(
            slug,
            "failed",
            error=f"delivery riwayah {config_riwayah!r} but shards are {shard_riwayah!r}",
            exit_code=17,
        )
    # Every consumer below walks `surah_info` to enumerate verses and to size
    # them, so it has to be this edition's counting profile, not Hafs's.
    from qua_shared.surah_words import surah_info_for, word_counts_for

    surah_info = surah_info_for(shard_riwayah, surah_info)

    # 2b. Gate incomplete verses: any verse missing a reference word index (never
    # recited) is dropped — no row, no audio slice. Coverage falls by that count.
    # The editor/TS tab still shows these (only the published artifacts gate).
    from qua_shared.timestamps_native import select_complete_verses

    canonical, dropped_incomplete = select_complete_verses(
        canonical, word_counts_for(shard_riwayah, surah_info)
    )
    if dropped_incomplete:
        log.info(
            "gated %d incomplete verse(s) (missing words) from %s: %s",
            len(dropped_incomplete),
            slug,
            dropped_incomplete,
        )
    timestamps = _reshape_timestamps_for_rows(canonical, digital_khatt_words)

    # 3. Build rows. source_url comes from the audio manifest's chapter URLs
    # (detailed.json carries no per-entry audio field). Prefer the manifest's
    # ``source_url`` (the original source, preserved when ``url`` was swapped
    # for a per-chapter bucket path on combined files) so the dataset keeps
    # provenance, not the internal bucket URL. ``source_offset_ms`` is where the
    # chapter begins inside that source — added to each clip's in-chapter start.
    detailed_by_ref = _detailed_by_ref(detailed)
    _manifest_chapters = (audio_manifest or {}).get("chapters") or {}
    chapter_urls = {
        str(ch): ((entry or {}).get("source_url") or (entry or {}).get("url", ""))
        for ch, entry in _manifest_chapters.items()
    }
    chapter_offsets = {
        str(ch): int((entry or {}).get("source_offset_ms") or 0)
        for ch, entry in _manifest_chapters.items()
    }
    rows = build_rows(
        timestamps,
        detailed_by_ref,
        surah_info,
        chapter_urls,
        chapter_offsets=chapter_offsets,
        pad_start=pad_start,
        pad_end=pad_end,
        min_gap=min_gap,
    )
    log.info("built %d rows for %s", len(rows), slug)
    if not rows:
        log.error("no rows built — detailed.json + timestamps disagreement?")
        return _result(
            slug, "failed", error="no rows built (detailed/timestamps disagree)", exit_code=15
        )

    # 4. Validate boundaries before any audio work.
    from qua_shared.dataset_validation import fatal_violations, validate_dataset

    summary = validate_dataset(_verses_for_validation(rows), surah_info=surah_info)
    fatal = fatal_violations(summary["violations"])
    if fatal:
        log.error("boundary validation failed: %d fatal violation(s)", len(fatal))
        for v in fatal[:5]:
            log.error("  %s", v)
        return _result(
            slug,
            "failed",
            validation_summary=summary,
            error=f"boundary validation failed ({len(fatal)} fatal)",
            exit_code=1,
        )

    # 5. Slice audio per row, in-process. Each chapter MP3 is read from the
    # bucket once (single bulk sequential read — FUSE/NFS hates random access),
    # its frame grid is parsed once, then every verse clip is a byte-exact copy
    # of the frames covering [clip_start, clip_end] — no per-clip ffmpeg/ffprobe.
    # Chapters are processed across a pool sized to the real CPU quota so bucket
    # reads + index builds overlap; per-chapter the slicing itself is cheap
    # pure-Python (bound by chapter count, not verse count).
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor, as_completed

    audio_bytes: list[bytes | None] = [None] * len(rows)
    failed_slices: list[str] = []

    rows_by_chapter: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for i, row in enumerate(rows):
        rows_by_chapter[int(row["chapter"])].append((i, row))

    def _slice_chapter(chapter: int, chapter_rows: list[tuple[int, dict]]):
        """Read + index one chapter MP3, slice all its verse rows in-process.

        Returns ``(produced, failures, stitched)`` where ``produced`` is a list
        of ``(row_index, clip_bytes)``, ``failures`` a list of ``"surah:ayah"``
        labels, and ``stitched`` the ``"surah:ayah"`` labels whose clip excised
        ≥1 interior no-match gap (>1 kept run). Contiguous verses (one run) take
        the original single ``_frame_slice`` path unchanged.
        """
        src_bucket = _chapter_mp3_path(slug, chapter)
        if not src_bucket.exists():
            return [], [f"{row['surah']}:{row['ayah']} (no audio)" for _, row in chapter_rows], []
        try:
            data = src_bucket.read_bytes()
        except OSError as exc:
            log.warning("ch %d: read failed: %s", chapter, exc)
            return [], [f"{row['surah']}:{row['ayah']} (read error)" for _, row in chapter_rows], []
        index = build_frame_index(data)
        if index.n_frames <= 0:
            return [], [f"{row['surah']}:{row['ayah']} (no frames)" for _, row in chapter_rows], []
        produced: list[tuple[int, bytes]] = []
        failures: list[str] = []
        stitched: list[str] = []
        for i, row in chapter_rows:
            keep_runs = row.get("keep_runs") or [(row["clip_start"], row["clip_end"])]
            if len(keep_runs) <= 1:
                # Common path: single contiguous run → byte-identical to before.
                cut = _frame_slice(data, index, row["clip_start"], row["clip_end"])
                if cut is None:
                    failures.append(f"{row['surah']}:{row['ayah']}")
                    continue
                clip_bytes, actual_start, _actual_end = cut
                _rebase_row(row, actual_start)
                produced.append((i, clip_bytes))
            else:
                # Interior no-match gap(s): stitch the kept runs, drop the gap
                # audio, rebase word/segment times gaplessly.
                ms: MultiFrameSlice | None = slice_frames_multi(data, index, keep_runs)
                if ms is None:
                    failures.append(f"{row['surah']}:{row['ayah']}")
                    continue
                _rebase_row_multi(row, ms.runs)
                produced.append((i, ms.data))
                stitched.append(f"{row['surah']}:{row['ayah']}")
        return produced, failures, stitched

    workers = _slice_workers()
    progress_every = max(50, len(rows) // 20)
    log.info(
        "slicing %d rows across %d chapters with %d workers (progress every %d)",
        len(rows),
        len(rows_by_chapter),
        workers,
        progress_every,
    )

    done = 0
    stitched_slices: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_slice_chapter, ch, rows_by_chapter[ch]): ch
            for ch in sorted(rows_by_chapter)
        }
        for fut in as_completed(futures):
            produced, failures, stitched = fut.result()
            for idx, blob in produced:
                audio_bytes[idx] = blob
            failed_slices.extend(failures)
            stitched_slices.extend(stitched)
            done += len(produced) + len(failures)
            if done % progress_every < (len(produced) + len(failures)) or done == len(rows):
                log.info("  sliced %d/%d (%d failed so far)", done, len(rows), len(failed_slices))
    sliced_count = sum(1 for b in audio_bytes if b is not None)
    log.info("audio: %d sliced, %d failed", sliced_count, len(failed_slices))
    if stitched_slices:
        log.info(
            "audio: %d verse(s) had interior no-match gaps excised: %s",
            len(stitched_slices),
            sorted(stitched_slices)[:20],
        )
    if failed_slices:
        log.warning("failed slices (first 20): %s", failed_slices[:20])
    if sliced_count == 0:
        log.error("every audio slice failed — refusing to push empty dataset")
        return _result(
            slug,
            "failed",
            validation_summary=summary,
            error="every audio slice failed",
            exit_code=16,
        )

    # 6. Push to HF — gets us a commit sha to record as ``version``.
    version_sha = _push_to_hf(slug, config_riwayah, rows, audio_bytes)

    repo_id = _resolve_dataset_repo_id()
    if sync_card:
        _sync_dataset_catalog_and_card(repo_id)

    external_uri = (
        f"https://huggingface.co/datasets/{repo_id}/tree/{version_sha}"
        if version_sha
        else f"https://huggingface.co/datasets/{repo_id}"
    )
    log.info("publish_hf: done slug=%s version=%s", slug, version_sha)
    return _result(
        slug,
        "succeeded",
        version=version_sha or job_id,
        external_uri=external_uri,
        validation_summary=summary,
    )


def main() -> int:
    slug = os.environ.get("SLUG", "").strip()
    job_id = os.environ.get("JOB_ID", "").strip() or "unknown"
    pads = pad_params_from_env()
    pad_start, pad_end, min_gap = pads["pad_start"], pads["pad_end"], pads["min_gap"]
    log.info(
        "publish_hf: slug=%s job=%s pads=(start=%d,end=%d,gap=%d)",
        slug,
        job_id,
        pad_start,
        pad_end,
        min_gap,
    )

    result = publish_slug(
        slug, job_id, sync_card=True, pad_start=pad_start, pad_end=pad_end, min_gap=min_gap
    )
    _post_webhook(
        slug=slug,
        job_id=job_id,
        version=result["version"] or job_id,
        external_uri=result["external_uri"],
        status=result["status"],
        validation_summary=result["validation_summary"],
    )
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
