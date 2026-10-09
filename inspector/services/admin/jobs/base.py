"""Shared infrastructure for kind-dispatched HF Job launches.

One file per ``kind`` owns its launch params + ``complete()`` handler. This
module factors out the cross-kind plumbing: HF Job mechanics (run_job,
status polling, log fetching, terminal-state dispatch), per-slug single-flight
across kinds + global single-flight for ``cut_release``, the
``reciters/<slug>/jobs/<kind>/<id>.json`` record paths, and the webhook /
poll-fallback completion router.

The HF Job container NEVER writes the DB — it writes bucket artifacts only.
DB mutations happen via the per-kind ``complete()`` handlers, called either
by the webhook receiver in ``routes/webhooks`` or by the in-process poll
fallback (every 120s) defined here.
"""

from __future__ import annotations

import datetime
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from services.storage.hf_bucket import StorageNotFound, get_backend

log = logging.getLogger("inspector")


# ---------------------------------------------------------------------------
# Terminal HF stages.
# ---------------------------------------------------------------------------

TERMINAL = (
    "succeeded",
    "completed",
    "failed",
    "error",
    "errored",
    "timed-out",
    "timeout",
    "stopped",
    "canceled",
    "cancelled",
    "deleted",
)
TERMINAL_SUCCESS = ("succeeded", "completed")

# Reciter-label sentinels for jobs that aren't scoped to a single slug:
# ``_global`` (cut_release) and ``_batch`` (hf_publish_batch). Both normalize
# to ``slug=None`` on the wire.
GLOBAL_SLUGS = (None, "_global", "_batch")


# ---------------------------------------------------------------------------
# Repo root + aligner bucket + base job image. Same defaults as the legacy
# timestamps_jobs.py — every kind re-uses them.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[4]
ALIGNER_BUCKET = os.environ.get("INSPECTOR_ALIGNER_BUCKET", "hetchyy/aligner-bucket")
#: Stock image every job kind runs in. Nothing is prebaked: each launch apt-installs
#: ffmpeg (acquire encodes with it; torchcodec needs its libs) and pip-installs
#: the kind's deps — ~40 s per job, no image Space to keep alive.
JOB_IMAGE = os.environ.get("INSPECTOR_JOB_IMAGE", "python:3.11-slim")
_APT = "apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends ffmpeg >/dev/null"


#: Installed for EVERY kind, not per-kind: ``huggingface_hub`` is the bucket client,
#: ``brotli`` is imported at module scope by ``qua_shared.verse_layout`` (the
#: ``.br`` timestamps shards), and ``pydantic`` backs every ``qua_shared.schemas``
#: model (``qua_shared.hf_dataset_catalog`` imports it at module scope) — all of
#: which the release entrypoints pull in transitively.
_BASE_DEPS = "huggingface_hub brotli pydantic"

#: Where the runtime image keeps the optional ``qua_domain`` wheel (Dockerfile
#: stage 1b -> ``/opt/wheels``). ``stage_job_code`` ships it to the aligner
#: bucket under ``code/wheels/`` and ``job_command`` installs it, so a non-Hafs
#: publish / cut projects into the riwayah's coordinates exactly like the Space.
#: Absent wheel = Hafs-only jobs, the same degradation as the Space itself.
QUA_DOMAIN_WHEEL_DIR = Path(os.environ.get("INSPECTOR_QUA_DOMAIN_WHEEL_DIR", "/opt/wheels"))
QUA_DOMAIN_WHEEL_GLOB = "qua_domain-*.whl"
STAGED_WHEEL_DIR = "code/wheels"
#: Glob-guarded so a Hafs-only image (nothing staged) still launches jobs.
_QUA_DOMAIN_INSTALL = (
    f"if ls /aux/{STAGED_WHEEL_DIR}/{QUA_DOMAIN_WHEEL_GLOB} >/dev/null 2>&1; then "
    f"pip install -q --root-user-action=ignore --no-deps "
    f"/aux/{STAGED_WHEEL_DIR}/{QUA_DOMAIN_WHEEL_GLOB}; "
    "else echo 'no qua-domain wheel staged - Hafs-only job'; fi"
)


def job_command(entrypoint: str, deps: str = "") -> list[str]:
    """The ``bash -lc`` command for a job: system deps, pip deps, the staged
    ``qua_domain`` wheel (if any), then the entrypoint."""
    pip = f"pip install -q --root-user-action=ignore {_BASE_DEPS} {deps}".rstrip()
    return ["bash", "-lc", f"{_APT} && {pip} && {_QUA_DOMAIN_INSTALL} && {entrypoint}"]


def qua_domain_wheel() -> Path | None:
    """The ``qua_domain`` wheel baked into this image, or ``None`` (Hafs-only)."""
    if not QUA_DOMAIN_WHEEL_DIR.is_dir():
        return None
    return next(iter(sorted(QUA_DOMAIN_WHEEL_DIR.glob(QUA_DOMAIN_WHEEL_GLOB))), None)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def hf_job_id(job) -> str | None:
    return getattr(job, "id", None) or getattr(job, "job_id", None)


def hf_status_str(info) -> str:
    status = getattr(info, "status", None)
    stage = getattr(status, "stage", None)
    return str(stage if stage is not None else status or "").lower()


_hf_namespace: str | None = None
_hf_namespace_resolved = False


def hf_job_url(job_id: str) -> str | None:
    """Best-effort HF job page URL ``https://huggingface.co/jobs/<ns>/<id>``.

    Used to backfill ``url`` for records sourced from the DB / a backfill (which
    never captured the live job object's ``.url``). The namespace is the account
    the HF token runs jobs under — resolved once via ``whoami()`` and cached.
    Returns None when the namespace can't be resolved (link is then hidden)."""
    global _hf_namespace, _hf_namespace_resolved
    if not job_id:
        return None
    if not _hf_namespace_resolved:
        _hf_namespace_resolved = True
        try:
            from huggingface_hub import whoami

            _hf_namespace = (whoami() or {}).get("name")
        except Exception as exc:
            log.warning("hf_job_url: whoami() failed: %s", exc)
            _hf_namespace = None
    if not _hf_namespace:
        return None
    return f"https://huggingface.co/jobs/{_hf_namespace}/{job_id}"


# ---------------------------------------------------------------------------
# Job-record paths: ``reciters/<slug>/jobs/<kind>/<job_id>.json`` for per-slug
# kinds, ``jobs/_global/<kind>/<job_id>.json`` for global kinds (cut_release).
#
# The on-disk DIRECTORY can differ from the kind label: ``timestamps`` records
# (written by the TS HF-Job + the legacy ``timestamps_jobs`` writer) live under
# ``jobs/ts/`` — so the unified store reads/writes them there, not under
# ``jobs/timestamps/``. Every other kind's dir == its label.
# ---------------------------------------------------------------------------

_KIND_DIR = {"timestamps": "ts"}


def kind_dir(kind: str) -> str:
    """Bucket sub-directory for a job kind (``timestamps`` → ``ts``)."""
    return _KIND_DIR.get(kind, kind)


def job_record_path(kind: str, slug: str | None, job_id: str) -> str:
    d = kind_dir(kind)
    if slug is None:
        return f"jobs/_global/{d}/{job_id}.json"
    return f"reciters/{slug}/jobs/{d}/{job_id}.json"


def legacy_job_record_path(kind: str, job_id: str) -> str:
    """Pre-2026-05 top-level path (kept for back-compat reads, never written)."""
    return f"jobs/{kind_dir(kind)}/{job_id}.json"


# ---------------------------------------------------------------------------
# Single-flight registry: per-slug across kinds + global lock for cut_release.
# ---------------------------------------------------------------------------


def running_job_for(*, kind: str | None = None, slug: str | None = None) -> tuple[str, str] | None:
    """Return ``(kind, job_id)`` of an in-flight job matching the filter, else None.

    ``kind=None, slug=X`` → any kind for that slug (cross-kind mutex).
    ``kind=K, slug=X``    → that specific kind for that slug.
    ``kind=K, slug=None`` → any global instance of that kind (e.g. cut_release).
    """
    from huggingface_hub import list_jobs

    try:
        for job in list_jobs():
            labels = getattr(job, "labels", {}) or {}
            j_kind = labels.get("task")
            j_slug = labels.get("reciter")
            if j_kind is None:
                continue
            if kind is not None and j_kind != kind:
                continue
            if slug is not None and j_slug != slug:
                continue
            if slug is None and kind is not None and j_slug not in GLOBAL_SLUGS:
                continue
            status = hf_status_str(job)
            if status in ("running", "pending", "updating"):
                return str(j_kind), (hf_job_id(job) or "")
    except Exception as exc:
        log.warning("running_job_for(kind=%s, slug=%s) failed: %s", kind, slug, exc)
    # Timestamps runs on the batch Space, not an HF Job (ADR 0002 slice B) — the
    # cross-kind per-slug mutex must still see one, from its bucket run-log.
    if slug is not None and kind in (None, "timestamps"):
        try:
            from services.admin import timestamps_jobs

            run_id = timestamps_jobs.running_job_for(slug)
            if run_id:
                return "timestamps", run_id
        except Exception as exc:  # noqa: BLE001 — never block on the bucket read
            log.warning("running_job_for ts-space check (slug=%s) failed: %s", slug, exc)
    return None


def kind_for_job(job_id: str) -> str | None:
    """Return the ``labels.task`` (kind) of the HF Job ``job_id``, or None.

    Used by the generic release-job cancel route to pick the right capability
    gate per kind without the caller already knowing the kind."""
    from huggingface_hub import list_jobs

    try:
        for job in list_jobs():
            if (hf_job_id(job) or "") == job_id:
                labels = getattr(job, "labels", {}) or {}
                return labels.get("task")
    except Exception as exc:
        log.warning("kind_for_job(%s) failed: %s", job_id, exc)
    return None


def kind_and_slug_for_job(job_id: str) -> tuple[str | None, str | None]:
    """Return ``(kind, slug)`` from an HF job's labels, ``(None, None)`` if not
    found. ``slug`` is None for global kinds (``_global`` / ``_batch``). Used by
    the cancel route to stamp the right job record terminal."""
    from huggingface_hub import list_jobs

    try:
        for job in list_jobs():
            if (hf_job_id(job) or "") == job_id:
                labels = getattr(job, "labels", {}) or {}
                slug = labels.get("reciter")
                if slug in ("_global", "_batch"):
                    slug = None
                return labels.get("task"), slug
    except Exception as exc:
        log.warning("kind_and_slug_for_job(%s) failed: %s", job_id, exc)
    return None, None


def _fetch_in_flight(kinds: tuple[str, ...]) -> list[dict]:
    """Network walk: live HF jobs filtered to running ``kinds``, cache + return.

    On any failure returns ``[]`` WITHOUT caching so the next call retries the
    HF API. Shared by the blocking read and the background SWR refresh."""
    from huggingface_hub import list_jobs

    from services.storage import cache as _cache

    out: list[dict] = []
    try:
        for job in list_jobs():
            labels = getattr(job, "labels", {}) or {}
            j_kind = labels.get("task")
            if j_kind not in kinds:
                continue
            status = hf_status_str(job)
            if status not in ("running", "pending", "updating"):
                continue
            slug = labels.get("reciter")
            if slug in ("_global", "_batch"):
                slug = None
            # ``created_at`` on the HF Jobs SDK is a datetime; ISO-format it
            # for the wire. ``started_at`` is set when execution actually
            # begins (post-pending) — prefer it when present.
            started = getattr(job, "started_at", None) or getattr(job, "created_at", None)
            if started is not None and not isinstance(started, str):
                try:
                    started = started.isoformat()
                except Exception:
                    started = None
            out.append(
                {
                    "kind": j_kind,
                    "slug": slug,
                    "job_id": hf_job_id(job) or "",
                    "started_at": started,
                    # HF job page URL — the FE renders an "Open on HF" link so
                    # the operator can watch logs / the job stream directly.
                    "url": getattr(job, "url", None),
                }
            )
    except Exception as exc:
        log.warning("list_in_flight_jobs(%s) failed: %s", kinds, exc)
        return []
    # Timestamps runs on the batch Space (ADR 0002 slice B), not an HF Job, so
    # ``list_jobs`` never yields them — fold in the bucket run-log's running run.
    if "timestamps" in kinds:
        try:
            from services.admin import timestamps_jobs

            out.extend(timestamps_jobs.in_flight_runs())
        except Exception as exc:  # noqa: BLE001 — never fail the whole listing
            log.warning("in-flight ts-space fold-in failed: %s", exc)
    _cache.set_in_flight_jobs_cache(kinds, out)
    return out


_inflight_refresh_lock = threading.Lock()
_inflight_refreshing: set[tuple[str, ...]] = set()


def _spawn_in_flight_refresh(kinds: tuple[str, ...]) -> None:
    """Kick one background refresh for ``kinds`` (deduped across concurrent polls).

    The refresh touches only cache globals + the HF SDK — no Flask/app context —
    so a bare daemon thread is safe on the gthread worker (the network wait
    releases the GIL)."""
    with _inflight_refresh_lock:
        if kinds in _inflight_refreshing:
            return
        _inflight_refreshing.add(kinds)

    def _run() -> None:
        try:
            _fetch_in_flight(kinds)
        finally:
            with _inflight_refresh_lock:
                _inflight_refreshing.discard(kinds)

    threading.Thread(target=_run, name="in-flight-jobs-refresh", daemon=True).start()


def list_in_flight_jobs(kinds: tuple[str, ...], *, block: bool = True) -> list[dict]:
    """Return ``[{kind, slug, job_id, started_at}, ...]`` for live jobs whose
    ``labels.task`` is in ``kinds``. ``slug`` is None for global kinds.

    Surfaces "what's running right now" to the operator. Wrapped in a 5 s TTL
    cache (``services/storage/cache.py``) — ``list_jobs()`` is rate-limited at
    the HF side and the FE polls every 30 s; the cache absorbs the poll +
    same-page re-renders cheaply. ``hf_publish.launch`` / ``cut_release.launch``
    and their ``complete()`` handlers invalidate so a freshly launched (or
    just-terminated) job shows up promptly.

    ``block`` (default True) does the synchronous network fetch on a cache miss
    — required by the automation reconciler, which must see accurate live state
    to avoid double-launching. ``block=False`` is stale-while-revalidate for the
    user-facing releases-status route: a cache miss kicks a background refresh
    and returns the last-known value (or ``[]``) immediately, so the page never
    blocks on the network call.
    """
    from services.storage import cache as _cache

    cached = _cache.get_in_flight_jobs_cache(kinds)
    if cached is not None:
        return cached
    if block:
        return _fetch_in_flight(kinds)
    _spawn_in_flight_refresh(kinds)
    stale = _cache.get_in_flight_jobs_cache_stale(kinds)
    return stale if stale is not None else []


# ---------------------------------------------------------------------------
# Job-record I/O — kind-aware. Read prefers the v2 per-kind path, falls back
# to the legacy top-level path so old records still surface.
# ---------------------------------------------------------------------------


def write_record_bytes(kind: str, slug: str | None, job_id: str, payload: bytes) -> None:
    try:
        get_backend().write_bytes_atomic(job_record_path(kind, slug, job_id), payload)
    except Exception as exc:
        log.warning("write job record %s/%s/%s failed: %s", kind, slug, job_id, exc)


def read_record_bytes(kind: str, slug: str | None, job_id: str) -> bytes | None:
    backend = get_backend()
    for path in (
        job_record_path(kind, slug, job_id),
        legacy_job_record_path(kind, job_id),
    ):
        try:
            return backend.read_bytes(path)
        except StorageNotFound:
            continue
        except Exception as exc:
            log.warning("read job record %s failed: %s", job_id, exc)
            return None
    return None


# ---------------------------------------------------------------------------
# Stage shared script code once per launch. Same idempotent pattern as v1.
# ---------------------------------------------------------------------------


# Files every launch MUST get into ``aligner-bucket/code/`` for the HF Job to
# run. Loud preflight prevents silent skips when the deployed image is missing
# something (e.g. a new entrypoint shipped but Dockerfile not updated).
REQUIRED_ENTRYPOINTS = (
    "qua_jobs/publish_hf.py",
    "qua_jobs/publish_hf_batch.py",
    "qua_jobs/refresh_hf_catalog.py",
    "qua_jobs/cut_release.py",
    "qua_jobs/shard.py",
    "qua_jobs/check_updates.py",
    "qua_jobs/download_audio.py",
    "qua_jobs/acquire_audio.py",
    "qua_jobs/split_audio.py",
    "qua_jobs/audio_io.py",
)
REQUIRED_STATIC_FILES = (
    "data/digital_khatt_v2_script.json",
    "data/surah_info.json",
    "inspector/frontend/public/fonts/DigitalKhattV2.otf",
    ".github/config/repo.yml",
    "docs/templates/release_body.md",
    "docs/templates/hf_dataset_card.md",
    "LICENSE",
    "LICENSE-DATA",
)


class JobStagingError(RuntimeError):
    """Raised when stage_job_code can't satisfy its file contract.

    Surfaced before ``run_job`` so the operator gets a clear deploy-fix
    message instead of an opaque ``No such file or directory`` from the
    container.
    """


def _is_lfs_pointer(p: Path) -> bool:
    """True if ``p`` is a git-LFS pointer stub, not the real bytes. On an HF
    Space build, ``*.gz`` files are LFS'd by extension and reach the image
    unsmudged (a ~130-byte ``version https://git-lfs…`` stub)."""
    try:
        with p.open("rb") as f:
            return f.read(40).startswith(b"version https://git-lfs")
    except OSError:
        return False


def _resolve_required_static(rel: str) -> Path | None:
    """Resolve one required staged file, rejecting LFS pointer stubs."""
    src = REPO_ROOT / rel
    if src.exists() and not _is_lfs_pointer(src):
        return src
    return None


def stage_job_code() -> None:
    """Upload ``qua_shared`` + ``qua_jobs`` + static refs to
    ``aligner-bucket/code/`` so the HF Job container can import them.

    Walks ``qua_shared`` and ``qua_jobs`` for every ``.py`` then appends
    the curated static files. Raises ``JobStagingError`` BEFORE the upload if
    any required entrypoint or static file can't be resolved on disk —
    catches the "Dockerfile missing a COPY" class of regression at launch.

    Idempotent — Xet de-dupes unchanged content.
    """
    from huggingface_hub import batch_bucket_files

    adds: list[tuple[str | Path | bytes, str]] = []
    seen_targets: set[str] = set()

    for sub in ("qua_shared", "qua_jobs"):
        base = REPO_ROOT / sub
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            adds.append((str(path), f"code/{rel}"))
            seen_targets.add(f"code/{rel}")

    for rel in REQUIRED_STATIC_FILES:
        path = _resolve_required_static(rel)
        target = f"code/{rel}"
        if path is not None:
            adds.append((str(path), target))
            seen_targets.add(target)

    wheel = qua_domain_wheel()
    if wheel is not None:
        adds.append((str(wheel), f"{STAGED_WHEEL_DIR}/{wheel.name}"))
    else:
        log.warning(
            "stage_job_code: no qua_domain wheel under %s - jobs run Hafs-only "
            "(a non-Hafs publish/cut will fail)",
            QUA_DOMAIN_WHEEL_DIR,
        )

    expected = {f"code/{p}" for p in REQUIRED_ENTRYPOINTS + REQUIRED_STATIC_FILES}
    missing = sorted(expected - seen_targets)
    if missing:
        raise JobStagingError(
            "stage_job_code: required files missing from the running image: "
            f"{missing}. Likely cause: inspector/Dockerfile doesn't COPY one "
            "of these paths. Fix the Dockerfile, redeploy the Space, then retry."
        )

    batch_bucket_files(ALIGNER_BUCKET, add=adds)
    log.info("staged %d job-code files to %s/code/", len(adds), ALIGNER_BUCKET)


# ---------------------------------------------------------------------------
# Poll-fallback worker registry + driver.
#
# Each kind registers a (TERMINAL_SUCCESS, complete()) handler; the worker
# polls both live HF Jobs and the timestamp Space's bucket run records every
# 120s, then dispatches terminal completions to the right handler.
# ---------------------------------------------------------------------------

PollHandler = Callable[[str | None, str], None]
_HANDLERS: dict[str, PollHandler] = {}
POLL_INTERVAL_SECONDS = 120


def register_handler(kind: str, fn: PollHandler) -> None:
    """Register a kind's terminal-success complete() handler for poll dispatch.

    ``fn(slug, job_id)`` is called once per terminal job; ``slug`` is None for
    global kinds (``cut_release``). The handler must be idempotent — both
    webhook + poll can fire concurrently.
    """
    _HANDLERS[kind] = fn


#: Kinds whose completion CAN run on the poll path — their ``complete()`` does an
#: idempotent DB write from ``(slug, job_id)`` alone. ``cut_release`` is excluded
#: on purpose: it is webhook-only (its complete() needs the full members payload
#: the poll path cannot reconstruct).
POLL_COMPLETABLE_KINDS = ("timestamps", "hf_publish", "hf_publish_batch", "refresh_catalog")


def register_poll_handlers() -> None:
    """Register every poll-completable kind's handler in one place.

    Single source of truth for boot wiring so a kind can't be silently dropped —
    ``ts`` was, which left automated TS regens (no webhook, no open drawer) with
    no completion path at all: ``produced_at`` never advanced, so the recitation
    stayed permanently behind-edits and the auto-regen automation relaunched every
    tick. Deferred imports avoid a circular import at module load."""
    from . import hf_publish, hf_publish_batch, refresh_catalog, ts

    ts.register()
    hf_publish.register()
    hf_publish_batch.register()
    refresh_catalog.register()


# (kind, job_id) pairs already driven to completion this process. HF keeps a
# terminal job in ``list_jobs()`` for a retention window, so without this memo
# the poll re-dispatches every terminal job each tick — and each idempotent
# handler re-opens a durable_transaction that re-pushes the whole DB to the
# bucket for a no-op. Bounded to jobs HF still lists (see end of the tick).
_completed_jobs: set[tuple[str, str]] = set()


def _poll_terminal_jobs() -> None:
    """Scan Space runs and HF Jobs, dispatching newly terminal successes."""
    terminal_seen: set[tuple[str, str]] = set()

    # Timestamp generation moved from ephemeral HF Jobs to a persistent Space.
    # Its bucket run record is the process-independent completion signal.
    try:
        from services.admin import timestamps_jobs

        timestamp_runs = timestamps_jobs.terminal_success_runs()
    except Exception as exc:  # noqa: BLE001
        log.warning("poll worker timestamp Space scan failed: %s", exc)
        timestamp_runs = []
    for slug, jid in timestamp_runs:
        key = ("timestamps", jid)
        terminal_seen.add(key)
        if key in _completed_jobs:
            continue
        handler = _HANDLERS.get("timestamps")
        if handler is None:
            continue
        try:
            handler(slug, jid)
        except Exception as exc:  # noqa: BLE001
            log.warning("poll handler timestamps(%s, %s) failed: %s", slug, jid, exc)
        else:
            _completed_jobs.add(key)

    from huggingface_hub import list_jobs

    try:
        jobs = list(list_jobs())
    except Exception as exc:
        log.warning("poll worker list_jobs failed: %s", exc)
        _completed_jobs.intersection_update(terminal_seen)
        return
    for job in jobs:
        labels = getattr(job, "labels", {}) or {}
        kind = labels.get("task")
        slug = labels.get("reciter")
        if kind not in _HANDLERS:
            continue
        status = hf_status_str(job)
        if status not in TERMINAL_SUCCESS:
            continue
        jid = hf_job_id(job)
        if not jid:
            continue
        key = (kind, jid)
        terminal_seen.add(key)
        if key in _completed_jobs:
            continue  # already completed this process — don't re-fire the handler
        slug_arg = None if slug in ("_global", "_batch") else slug
        try:
            _HANDLERS[kind](slug_arg, jid)
        except Exception as exc:
            log.warning("poll handler %s(%s, %s) failed: %s", kind, slug_arg, jid, exc)
        else:
            _completed_jobs.add(key)
    # Forget jobs HF no longer lists so the memo can't grow unbounded over a
    # long-lived worker; a job that reappears just re-fires once.
    _completed_jobs.intersection_update(terminal_seen)


_poll_thread: threading.Thread | None = None
_poll_stop = threading.Event()


def start_poll_worker() -> None:
    """Idempotent — start a single daemon thread that polls every 120s."""
    global _poll_thread
    if _poll_thread is not None and _poll_thread.is_alive():
        return

    def _loop() -> None:
        while not _poll_stop.wait(POLL_INTERVAL_SECONDS):
            _poll_terminal_jobs()

    _poll_thread = threading.Thread(target=_loop, name="jobs-poll-worker", daemon=True)
    _poll_thread.start()
    log.info("jobs poll worker started (interval=%ds)", POLL_INTERVAL_SECONDS)


def stop_poll_worker() -> None:
    """Used by tests."""
    _poll_stop.set()
    _completed_jobs.clear()
