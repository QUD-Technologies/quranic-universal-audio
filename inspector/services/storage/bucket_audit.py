"""Validate every artefact in a bucket ``reciters/<slug>/`` folder against the
canonical ``qua_shared/schemas`` definitions.

This is the Flask-free engine behind the steady-state external-bucket drift
harness. It walks one reciter folder on any storage backend and audits every
file we expect to find there:

* ``detailed.json``            -> ``DetailedDocument`` (pydantic v2)
* ``segments.json``            -> structural check (no formal schema yet)
* ``edit_history.jsonl``       -> ``parse_edit_history_line`` (per JSONL row)
* ``edit_history_peaks.jsonl`` -> ``parse_peaks_record`` (per JSONL row)
* ``low_confidence_v2.json``   -> structural check (``failures`` is list[str])
* ``auto_split_v1.json``       -> structural check (``by_uid`` dict of slim entries)
* ``hidden_pause_v1.json``     -> structural check (``by_uid`` dict keyed by segment uid)
* ``missed_waqf_v1.json``      -> structural check (``by_uid`` dict keyed by segment uid)
* ``false_split_v1.json``      -> structural check (``by_uid`` dict keyed by segment uid)
* ``unmarked_wasl_v1.json``    -> structural check (``by_uid`` dict keyed by segment uid)
* ``wasl_recheck_v1.json``     -> structural check (``by_uid`` dict keyed by segment uid)
* ``pipeline_meta.json``       -> ``PipelineMeta`` (pydantic v2)
* ``audio/_done.json``         -> sentinel parse
* ``audio/<ch>.mp3``           -> existence + non-empty (3-chapter sample)
* ``peaks/<ch>.json.gz``       -> v3 slim round-trip (3-chapter sample)

The schemas are pure ``extra="forbid"``: an unexpected/legacy field raises a
``ValidationError``, which the per-file auditors catch and report as
``status="error"`` — so writer/schema drift is caught at parse time rather
than silently stripped.

Backend-agnostic by design: every auditor operates on any object exposing
``read_bytes(path: str) -> bytes`` (raising on a missing path) and
``list_dir(prefix: str) -> list[str]``. In production that is the singleton
``services.storage.hf_bucket.get_backend()``; tests pass a ``FilesystemBackend``
against committed fixtures. The CLI wrapper lives in
``scripts/bucket/audit_bucket_reciter.py``.

Public surface:
    ``audit(backend, bucket_id, slug) -> AuditResult`` — the per-reciter walker.
    ``sample_validation(...) -> dict`` — the cheap catalog + small-sample drift
        probe wired into ``/healthz?deep=1`` so a deployed Space surfaces
        external-file drift at health-check time instead of mid-request.
    ``AuditResult`` / ``FileResult`` — result dataclasses.
    ``TOP_LEVEL_AUDITORS`` — the ordered ``(suffix, fn, required)`` table.
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@contextmanager
def _capture_extras_warnings():
    """Capture INFO/WARNING records from the schema extras logger so the
    audit surfaces every legacy + unknown field stripped during parse.

    Yields a list that fills with ``(level, message)`` tuples — the caller
    can summarise it into the per-file FileResult detail / warnings counts.
    """
    captured: list[tuple[int, str]] = []
    extras_log = logging.getLogger("qua_shared.schemas._extras")
    prev_level = extras_log.level
    extras_log.setLevel(logging.INFO)

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append((record.levelno, record.getMessage()))

    sink = _Sink(level=logging.INFO)
    extras_log.addHandler(sink)
    try:
        yield captured
    finally:
        extras_log.removeHandler(sink)
        extras_log.setLevel(prev_level)


def _summarise_extras(captured: list[tuple[int, str]]) -> str:
    """Compress N captured records into a single ``detail`` suffix."""
    if not captured:
        return ""
    n_info = sum(1 for lvl, _ in captured if lvl == logging.INFO)
    n_warn = sum(1 for lvl, _ in captured if lvl >= logging.WARNING)
    bits: list[str] = []
    if n_info:
        bits.append(f"{n_info} legacy")
    if n_warn:
        bits.append(f"{n_warn} unknown")
    # Sample the first WARNING (most concerning class).
    sample = next((m for lvl, m in captured if lvl >= logging.WARNING), None)
    if sample is None and captured:
        sample = captured[0][1]
    suffix = " + ".join(bits) + " extras"
    return f"; {suffix} (e.g. {sample[:160]})" if sample else f"; {suffix}"


@dataclass
class FileResult:
    path: str
    status: str  # "ok" | "missing" | "error"
    detail: str = ""
    size: int = 0
    items_ok: int = 0  # for JSONL / collections
    items_total: int = 0
    n_warn: int = 0  # # of "unknown field" warnings from extras pre-validator
    n_info: int = 0  # # of "legacy field" infos from extras pre-validator


@dataclass
class AuditResult:
    slug: str
    bucket_id: str
    found: bool  # whether reciters/<slug>/ exists
    files: list[FileResult] = field(default_factory=list)

    def add(self, f: FileResult) -> None:
        self.files.append(f)

    @property
    def n_errors(self) -> int:
        return sum(1 for f in self.files if f.status == "error")

    @property
    def n_missing(self) -> int:
        return sum(1 for f in self.files if f.status == "missing")

    @property
    def n_warnings(self) -> int:
        return sum(f.n_warn for f in self.files)

    @property
    def n_legacy(self) -> int:
        return sum(f.n_info for f in self.files)


# ---------------------------------------------------------------------------
# Per-file validators
# ---------------------------------------------------------------------------


def _audit_detailed(backend, path: str) -> FileResult:
    from qua_shared.schemas import DetailedDocument

    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    with _capture_extras_warnings() as captured:
        try:
            validated = DetailedDocument.model_validate(doc)
        except Exception as e:
            return FileResult(path, "error", f"schema fail: {e}", size=size)

    n_segs = sum(len(e.segments) for e in validated.entries)
    n_info = sum(1 for lvl, _ in captured if lvl == logging.INFO)
    n_warn = sum(1 for lvl, _ in captured if lvl >= logging.WARNING)
    return FileResult(
        path,
        "ok",
        f"{len(validated.entries)} entries, {n_segs} segs" + _summarise_extras(captured),
        size=size,
        items_ok=n_segs,
        items_total=n_segs,
        n_info=n_info,
        n_warn=n_warn,
    )


def _audit_segments(backend, path: str) -> FileResult:
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    if not isinstance(doc, dict):
        return FileResult(path, "error", "top-level not a dict", size=size)
    meta = doc.get("_meta")
    if meta is not None and not isinstance(meta, dict):
        return FileResult(path, "error", "_meta is not a dict", size=size)

    n_verses = 0
    issues = []
    for key, rows in doc.items():
        if key == "_meta":
            continue
        n_verses += 1
        if not isinstance(rows, list):
            issues.append(f"{key}: rows not a list")
            continue
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) < 4:
                issues.append(
                    f"{key}[{i}]: row shape — need [w_start, w_end, ms_start, ms_end, ...]"
                )
                break
            # Per row: ints + optional dict (e.g. {"repeated": [...]})
            w_start, w_end, ms_start, ms_end = row[:4]
            if not (isinstance(w_start, int) and isinstance(w_end, int)):
                issues.append(f"{key}[{i}]: word range not ints")
                break
            if not (isinstance(ms_start, int) and isinstance(ms_end, int)):
                issues.append(f"{key}[{i}]: ms range not ints")
                break
            if ms_end < ms_start:
                issues.append(f"{key}[{i}]: ms_end < ms_start")
                break

    if issues:
        return FileResult(
            path,
            "error",
            f"{len(issues)} issue(s): {issues[0]}"
            + (f" (+{len(issues) - 1} more)" if len(issues) > 1 else ""),
            size=size,
        )
    return FileResult(
        path, "ok", f"{n_verses} verse keys", size=size, items_ok=n_verses, items_total=n_verses
    )


def _audit_edit_history(backend, path: str) -> FileResult:
    from qua_shared.schemas import parse_edit_history_line

    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    lines = raw.decode("utf-8").splitlines()
    n_ok = 0
    n_total = 0
    first_err = None
    captured: list[tuple[int, str]] = []
    with _capture_extras_warnings() as run_captured:
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            n_total += 1
            try:
                batch = parse_edit_history_line(line)
                if batch is None:
                    # legacy genesis line silently dropped (not an error)
                    continue
                n_ok += 1
            except Exception as e:
                if first_err is None:
                    first_err = f"line {i}: {e}"
        captured.extend(run_captured)

    n_info = sum(1 for lvl, _ in captured if lvl == logging.INFO)
    n_warn = sum(1 for lvl, _ in captured if lvl >= logging.WARNING)
    if first_err:
        return FileResult(
            path,
            "error",
            f"{n_ok}/{n_total} valid; first error: {first_err}",
            size=size,
            items_ok=n_ok,
            items_total=n_total,
            n_info=n_info,
            n_warn=n_warn,
        )
    return FileResult(
        path,
        "ok",
        f"{n_ok}/{n_total} batches" + _summarise_extras(captured),
        size=size,
        items_ok=n_ok,
        items_total=n_total,
        n_info=n_info,
        n_warn=n_warn,
    )


def _audit_peaks_history(backend, path: str) -> FileResult:
    from qua_shared.schemas import parse_peaks_record

    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    lines = raw.decode("utf-8").splitlines()
    n_ok = 0
    n_total = 0
    first_err = None
    captured: list[tuple[int, str]] = []
    with _capture_extras_warnings() as run_captured:
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            n_total += 1
            try:
                # ``parse_peaks_record`` expects an already-parsed dict,
                # unlike ``parse_edit_history_line`` which takes the raw
                # JSONL line string. Decode here.
                obj = json.loads(line)
                rec = parse_peaks_record(obj)
                if rec is None:
                    continue
                n_ok += 1
            except Exception as e:
                if first_err is None:
                    first_err = f"line {i}: {e}"
        captured.extend(run_captured)

    n_info = sum(1 for lvl, _ in captured if lvl == logging.INFO)
    n_warn = sum(1 for lvl, _ in captured if lvl >= logging.WARNING)
    if first_err:
        return FileResult(
            path,
            "error",
            f"{n_ok}/{n_total} valid; first error: {first_err}",
            size=size,
            items_ok=n_ok,
            items_total=n_total,
            n_info=n_info,
            n_warn=n_warn,
        )
    return FileResult(
        path,
        "ok",
        f"{n_ok}/{n_total} peaks records" + _summarise_extras(captured),
        size=size,
        items_ok=n_ok,
        items_total=n_total,
        n_info=n_info,
        n_warn=n_warn,
    )


def _audit_low_confidence_v2(backend, path: str) -> FileResult:
    """No formal schema — structural check.

    Expected shape:
        {"_meta": {...}, "failures": [str_uuid, ...]}
    """
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    if not isinstance(doc, dict):
        return FileResult(path, "error", "top-level not a dict", size=size)
    failures = doc.get("failures")
    if not isinstance(failures, list):
        return FileResult(path, "error", "failures missing or not a list", size=size)
    n_bad = sum(1 for u in failures if not isinstance(u, str))
    if n_bad:
        return FileResult(
            path, "error", f"{n_bad}/{len(failures)} failures are not strings", size=size
        )
    meta = doc.get("_meta")
    if meta is not None and not isinstance(meta, dict):
        return FileResult(path, "error", "_meta is not a dict", size=size)
    return FileResult(
        path,
        "ok",
        f"{len(failures)} probe failures @ beam={(meta or {}).get('beam')}",
        size=size,
        items_ok=len(failures),
        items_total=len(failures),
    )


def _audit_auto_split_v1(backend, path: str) -> FileResult:
    """No formal schema — structural check.

    Expected shape:
        {"_meta": {...}, "by_uid": {uid: {cursors: [int], refs: [str], kind: str}, ...}}
    """
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    if not isinstance(doc, dict):
        return FileResult(path, "error", "top-level not a dict", size=size)
    by_uid = doc.get("by_uid")
    if not isinstance(by_uid, dict):
        return FileResult(path, "error", "by_uid missing or not a dict", size=size)

    issues = []
    for uid, entry in by_uid.items():
        if not isinstance(entry, dict):
            issues.append(f"{uid[:8]}…: entry not a dict")
            continue
        for key, ty in (("cursors", list), ("refs", list), ("kind", str)):
            if key not in entry:
                issues.append(f"{uid[:8]}…: missing {key}")
                break
            if not isinstance(entry[key], ty):
                issues.append(f"{uid[:8]}…: {key} wrong type")
                break
        else:
            if entry["kind"] not in ("cross_verse", "repetition"):
                issues.append(
                    f"{uid[:8]}…: kind={entry['kind']!r} not in {{cross_verse, repetition}}"
                )

    if issues:
        return FileResult(path, "error", f"{len(issues)} issue(s); first: {issues[0]}", size=size)
    return FileResult(
        path,
        "ok",
        f"{len(by_uid)} entries",
        size=size,
        items_ok=len(by_uid),
        items_total=len(by_uid),
    )


def _audit_pipeline_meta(backend, path: str) -> FileResult:
    """``pipeline_meta.json`` — extraction-time facts (Inspector hard-fails
    on missing/invalid sidecar). Validated via the canonical Pydantic model."""
    from qua_shared.schemas import PipelineMeta

    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    with _capture_extras_warnings() as captured:
        try:
            validated = PipelineMeta.model_validate(doc)
        except Exception as e:
            return FileResult(path, "error", f"schema fail: {e}", size=size)

    n_ch = len(validated.deleted_basmala_chapters)
    n_info = sum(1 for lvl, _ in captured if lvl == logging.INFO)
    n_warn = sum(1 for lvl, _ in captured if lvl >= logging.WARNING)
    return FileResult(
        path,
        "ok",
        f"deleted_basmala_chapters={n_ch} @ {validated.generated_at}" + _summarise_extras(captured),
        size=size,
        items_ok=n_ch,
        items_total=n_ch,
        n_info=n_info,
        n_warn=n_warn,
    )


def _audit_done_sentinel(backend, path: str) -> FileResult:
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)
    return FileResult(path, "ok", "sentinel present + valid JSON", size=size)


def _audit_mp3(backend, path: str) -> FileResult:
    """Audio file — existence + non-empty. No format validation (would need
    ffprobe). The Xing-remux is verified at write time by extraction's
    audio_persist; in-bucket bytes are trusted.

    Sizes via a ``stat`` on the mount-backed local path so a deep audit /
    ``/healthz?deep=1`` never slurps a multi-MB audio file just to count
    bytes; only an unmounted (hffs) backend falls back to ``read_bytes``."""
    local = None
    try:
        local = backend.local_path(path)
    except Exception:  # noqa: BLE001 — treat any local-path failure as "no mount"
        local = None
    if local is not None:
        try:
            size = local.stat().st_size
        except OSError as e:
            return FileResult(path, "missing", str(e))
    else:
        try:
            size = len(backend.read_bytes(path))
        except Exception as e:  # noqa: BLE001 — missing/unreadable object
            return FileResult(path, "missing", str(e))
    if size < 1024:  # heuristic: even silence < 1 KB is suspicious
        return FileResult(path, "error", f"suspiciously small ({size}B)", size=size)
    return FileResult(path, "ok", f"{size:,}B", size=size)


def _audit_peaks_slim(backend, path: str) -> FileResult:
    """Inflate via the v3 slim round-trip (Migration #5 layout)."""
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))
    size = len(raw)
    try:
        # Inline the schema_version + duration_ms + peaks_b64 check rather
        # than importing inspector services (keeps this self-contained).
        decompressed = gzip.decompress(raw)
        doc = json.loads(decompressed)
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile) as e:
        return FileResult(path, "error", f"gunzip/JSON fail: {e}", size=size)
    if doc.get("schema_version") != 3:
        return FileResult(
            path, "error", f"schema_version={doc.get('schema_version')!r}, expected 3", size=size
        )
    if not doc.get("peaks_b64") or not doc.get("bps") or not doc.get("duration_ms"):
        return FileResult(path, "error", "missing peaks_b64 / bps / duration_ms", size=size)
    return FileResult(
        path,
        "ok",
        f"v3 slim, {doc.get('n')} pairs @ {doc.get('bps')}bps, {doc.get('duration_ms')}ms",
        size=size,
    )


# ---------------------------------------------------------------------------
# Top-level walker
def _audit_by_uid_sidecar(backend, path: str) -> FileResult:
    """Boundary-review sidecars (``hidden_pause_v1`` / ``missed_waqf_v1`` /
    ``false_split_v1`` / ``unmarked_wasl_v1`` / ``wasl_recheck_v1``).

    Expected shape:
        {"_meta": {...}, "by_uid": {uid: {"kind": str, "chapter": int, ...}}}
    """
    try:
        raw = backend.read_bytes(path)
    except Exception as e:
        return FileResult(path, "missing", str(e))

    size = len(raw)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        return FileResult(path, "error", f"invalid JSON: {e}", size=size)

    if not isinstance(doc, dict):
        return FileResult(path, "error", "top-level not a dict", size=size)
    by_uid = doc.get("by_uid")
    if not isinstance(by_uid, dict):
        return FileResult(path, "error", "by_uid missing or not a dict", size=size)
    meta = doc.get("_meta")
    if meta is not None and not isinstance(meta, dict):
        return FileResult(path, "error", "_meta is not a dict", size=size)
    n_bad = sum(1 for entry in by_uid.values() if not isinstance(entry, dict))
    if n_bad:
        return FileResult(path, "error", f"{n_bad}/{len(by_uid)} entries not dicts", size=size)
    return FileResult(
        path,
        "ok",
        f"{len(by_uid)} entries",
        size=size,
        items_ok=len(by_uid),
        items_total=len(by_uid),
    )


# ---------------------------------------------------------------------------


# Known artefacts in a reciter folder. (path-suffix, audit_fn, required).
TOP_LEVEL_AUDITORS: list[tuple[str, Callable, bool]] = [
    ("detailed.json", _audit_detailed, True),
    ("segments.json", _audit_segments, True),
    ("edit_history.jsonl", _audit_edit_history, True),
    ("edit_history_peaks.jsonl", _audit_peaks_history, False),
    ("low_confidence_v2.json", _audit_low_confidence_v2, False),
    ("auto_split_v1.json", _audit_auto_split_v1, False),
    ("hidden_pause_v1.json", _audit_by_uid_sidecar, False),
    ("missed_waqf_v1.json", _audit_by_uid_sidecar, False),
    ("false_split_v1.json", _audit_by_uid_sidecar, False),
    ("unmarked_wasl_v1.json", _audit_by_uid_sidecar, False),
    ("wasl_recheck_v1.json", _audit_by_uid_sidecar, False),
    # pipeline_meta.json is required: Inspector hard-fails on missing sidecar
    # to make backfill a deploy gate. Older reciters need
    # scripts/backfills/backfill_deleted_basmala.py.
    ("pipeline_meta.json", _audit_pipeline_meta, True),
    ("audio/_done.json", _audit_done_sentinel, False),
]


def _list_chapter_files(backend, prefix: str) -> list[str]:
    """List immediate children of ``<bucket>/<prefix>/``."""
    try:
        entries = backend.list_dir(prefix)
    except Exception:
        return []
    return entries or []


def audit(backend, bucket_id: str, slug: str) -> AuditResult:
    """Walk ``reciters/<slug>/`` on ``backend`` and audit every known artefact.

    ``backend`` is any object exposing ``read_bytes(path)`` (raising on a
    missing path) and ``list_dir(prefix)``. ``bucket_id`` is a display label
    only (stamped onto the result). Returns an ``AuditResult`` whose
    ``n_errors`` drives the caller's exit code.
    """
    base = f"reciters/{slug}"
    try:
        backend.read_bytes(f"{base}/detailed.json")
    except Exception:
        result = AuditResult(slug=slug, bucket_id=bucket_id, found=False)
        result.add(FileResult(f"reciters/{slug}/", "missing", "no detailed.json under reciters/"))
        return result
    result = AuditResult(slug=slug, bucket_id=bucket_id, found=True)

    # 1. Top-level JSON / JSONL artifacts.
    for suffix, fn, required in TOP_LEVEL_AUDITORS:
        path = f"{base}/{suffix}"
        fr = fn(backend, path)
        if fr.status == "missing" and not required:
            fr.detail = f"(optional — {fr.detail})"
        result.add(fr)

    # 2. Per-chapter audio + peaks.
    audio_children = _list_chapter_files(backend, f"{base}/audio/")
    peaks_children = _list_chapter_files(backend, f"{base}/peaks/")

    audio_chs = sorted(
        int(Path(p).stem)
        for p in audio_children
        if Path(p).suffix == ".mp3" and Path(p).stem.isdigit()
    )
    peaks_chs = sorted(
        int(Path(p).stem.replace(".json", "")) for p in peaks_children if p.endswith(".json.gz")
    )

    # Spot-check 3 chapters (first, middle, last) per side — full validation
    # would be 228 round-trips for a by_surah reciter.
    def _sample(xs):
        if not xs:
            return []
        if len(xs) <= 3:
            return list(xs)
        return [xs[0], xs[len(xs) // 2], xs[-1]]

    for ch in _sample(audio_chs):
        result.add(_audit_mp3(backend, f"{base}/audio/{ch}.mp3"))
    for ch in _sample(peaks_chs):
        result.add(_audit_peaks_slim(backend, f"{base}/peaks/{ch}.json.gz"))

    # Cross-check counts.
    n_audio = len(audio_chs)
    n_peaks = len(peaks_chs)
    parity_ok = n_audio == n_peaks
    detail = f"audio={n_audio} mp3, peaks={n_peaks} json.gz"
    if not parity_ok:
        detail += "  <-- MISMATCH"
    result.add(
        FileResult(
            f"{base}/(audio|peaks)/",
            "ok" if parity_ok else "error",
            detail,
            items_ok=min(n_audio, n_peaks),
            items_total=max(n_audio, n_peaks),
        )
    )

    return result


# ---------------------------------------------------------------------------
# Cheap deploy-time / health-check drift probe
# ---------------------------------------------------------------------------


# Default cap on how many reciter folders the health-check probe walks. A full
# sweep (every reciter) is the nightly CLI's job; the probe is a smoke test, so
# it stays bounded and cheap regardless of catalog size.
DEFAULT_SAMPLE_LIMIT = 3


def _sample_slugs(slugs: list[str], limit: int) -> list[str]:
    """Pick up to ``limit`` slugs spread across the sorted list (first / middle
    / last …) so a sample of 3 covers both ends of the catalog, not just the
    first three alphabetically. Deterministic for a given input."""
    ordered = sorted(slugs)
    if len(ordered) <= limit:
        return ordered
    if limit <= 0:
        return []
    if limit == 1:
        return [ordered[0]]
    # Evenly spaced indices including the first and last element.
    step = (len(ordered) - 1) / (limit - 1)
    idxs = sorted({round(i * step) for i in range(limit)})
    return [ordered[i] for i in idxs]


def sample_validation(
    *,
    backend=None,
    bucket_id: str = "",
    slugs: list[str] | None = None,
    limit: int = DEFAULT_SAMPLE_LIMIT,
) -> dict:
    """Cheap external-bucket drift probe for ``/healthz?deep=1`` and deploy gates.

    Two checks, both intentionally bounded so this is safe to run at health-check
    time:

    1. Validate the DB catalog by round-tripping it through
       ``repo_catalog.snapshot()`` (raises ``ValidationError`` on a bad row).
    2. Audit a SMALL sample of reciter folders (``limit`` of them, default 3)
       via :func:`audit`, spread across the sorted slug list.

    Everything is resolved lazily and defensively: a missing DB, an unreachable
    bucket, or a bad row degrades to ``ok=False`` with an ``errors`` entry rather
    than raising — the caller (a health route) must never 500 on this.

    Returns a compact, JSON-serialisable summary::

        {
          "ok": bool,                 # catalog_ok AND every sampled reciter clean
          "catalog_ok": bool,
          "sampled": ["slug-a", ...], # the slugs actually walked
          "n_reciters": int,          # total catalog size the sample was drawn from
          "errors": ["catalog: ...", "slug-a/detailed.json: ..."],
        }

    ``backend`` / ``bucket_id`` / ``slugs`` default to the live singleton +
    enumerated catalog; tests inject a ``FilesystemBackend`` + explicit slugs to
    stay offline.
    """
    errors: list[str] = []

    # 1. DB catalog round-trip.
    catalog_ok = True
    try:
        from services.db import repo_catalog

        repo_catalog.snapshot()
    except Exception as e:  # noqa: BLE001 — any failure is a degraded signal
        catalog_ok = False
        errors.append(f"catalog: {type(e).__name__}: {e}")

    # 2. Resolve backend + slugs lazily so the default /healthz path (which never
    # calls this) pays nothing.
    if backend is None:
        try:
            from services.storage.hf_bucket import get_backend

            backend = get_backend()
            bucket_id = bucket_id or type(backend).__name__
        except Exception as e:  # noqa: BLE001
            errors.append(f"backend: {type(e).__name__}: {e}")
            return {
                "ok": False,
                "catalog_ok": catalog_ok,
                "sampled": [],
                "n_reciters": 0,
                "errors": errors,
            }

    if slugs is None:
        try:
            from services.storage.data_dir import list_slugs

            slugs = list_slugs()
        except Exception as e:  # noqa: BLE001
            errors.append(f"list_slugs: {type(e).__name__}: {e}")
            slugs = []

    n_reciters = len(slugs)
    sample = _sample_slugs(slugs, limit)

    sampled_ok = True
    for slug in sample:
        try:
            res = audit(backend, bucket_id, slug)
        except Exception as e:  # noqa: BLE001 — never let one bad folder 500
            sampled_ok = False
            errors.append(f"{slug}: {type(e).__name__}: {e}")
            continue
        if res.n_errors:
            sampled_ok = False
            for f in res.files:
                if f.status == "error":
                    errors.append(f"{f.path}: {f.detail}")

    return {
        "ok": catalog_ok and sampled_ok,
        "catalog_ok": catalog_ok,
        "sampled": sample,
        "n_reciters": n_reciters,
        "errors": errors,
    }
