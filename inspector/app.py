"""
Alignment Inspector Server

Flask entry point: creates app, registers blueprints, serves the Vite-built
SPA shell (inspector/frontend/dist/) and cross-tab routes, and runs the
startup sequence.
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

# Ensure the repo root (parent of inspector/) is on sys.path so that
# `from qua_shared.X import Y` resolves to the sibling `qua_shared/` package
# (e.g. `boundary_check`, used by the timestamps validator) when the app is
# launched via `python3 inspector/app.py` from the repo root. Inside Docker
# the WORKDIR is /app and both /app/inspector/ and /app/scripts/ are present
# at that level, so this insert is also correct there.
_REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Local dev: hydrate process env from `<repo>/.env` if present. Production
# (HF Space) gets its secrets from Space settings and the file is absent.
# Keys already in the process env win (shell `export` beats the file).
def _load_dotenv_for_local_dev() -> None:
    # Load repo-root `.env` first, then `inspector/.env`. Keys already in the
    # process env win; earlier files win over later ones.
    for env_path in (_REPO_ROOT / ".env", Path(__file__).resolve().parent / ".env"):
        if not env_path.exists():
            continue
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except OSError:
            pass


_load_dotenv_for_local_dev()


# Auto-enable dev-mode auth bypass when running locally outside pytest.
# Tri-state env var: unset → auto-detect; "1" → force on; "0" → force off.
# Auto-detect: on iff INSPECTOR_BEHIND_PROXY != "1" AND not running under
# pytest (mirrors the audio-prefetch gate at module-load below). HF Space
# deploys set INSPECTOR_BEHIND_PROXY=1, so they never auto-enable.
if "INSPECTOR_DEV_MODE" not in os.environ:
    if os.environ.get("INSPECTOR_BEHIND_PROXY") != "1" and "pytest" not in sys.modules:
        os.environ["INSPECTOR_DEV_MODE"] = "1"


# Local dev: auto-mount the HF bucket via hf-mount so reads hit a local
# FUSE cache (~50-500x faster than going through hffs.cat_file every call).
# Mount path: inspector/.bucket/{dev,prod}/ (gitignored). Failures degrade
# silently to the API path — never blocks boot. See auto_mount.py for
# the full skip-conditions list.
from services.storage.auto_mount import auto_mount as _auto_mount_bucket

_auto_mount_bucket()


import uuid

from flask import Flask, g, jsonify, request, send_from_directory
from flask_compress import Compress
from routes import register_blueprints
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    ANON_COOKIE_MAX_AGE,
    ANON_COOKIE_NAME,
    DEFAULT_PORT,
    FLASK_DEV_VALUE,
    FLASK_ENV_VAR,
    SAMPLES_MAX_UPLOAD_BYTES,
    SERVER_HOST,
)
from qua_shared.riwayat import UnsupportedRiwayah
from services import auth as auth_service
from services import auto_detect as auto_detect_service
from services.admin import visitors as visitor_analytics
from services.data_loader import load_surah_info_lite
from services.errors import Codes, error_body
from services.reference.delivery_edition import RiwayahMismatch
from services.reference.editions import EditionsUnavailable, RefNotInEdition

# Phonemizer was eagerly initialized here. It's now imported lazily inside
# scripts/backfills/backfill_boundary_adj.py (the only remaining consumer).
from services.secrets_guard import MissingSecret, get_session_secret
from services.state.state import InvalidTransition, NotAuthorizedForTransition, UnknownReciter
from services.storage.hf_bucket import StorageReadOnly
from utils.json_response import orjson_response
from utils.wsgi import ForceHttpsScheme

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

_LEVEL_SHORT = {
    "CRITICAL": "CRIT",
    "WARNING": "WARN",
    "INFO": "INFO",
    "ERROR": "ERR ",
    "DEBUG": "DBG ",
}


class PlainFormatter(logging.Formatter):
    """Compact human-readable single-line format: ``HH:MM:SS LVL name | msg``.

    JSON aggregation was abandoned — HF Space logs and local stdout are both
    read by humans, and the JSON wrapper made every line wider than the
    actual message. Aggregators that need structure can grep on the level
    token; nothing in our pipeline ingests structured logs today.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, datefmt="%H:%M:%S")
        lvl = _LEVEL_SHORT.get(record.levelname, record.levelname[:4])
        name = record.name
        # Trim noisy package prefixes — "services.activity.activity_state"
        # → "activity_state" keeps the useful leaf without the breadcrumb.
        if name.startswith("services."):
            name = name.rsplit(".", 1)[-1]
        line = f"{ts} {lvl} {name} | {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _AccessLogFilter(logging.Filter):
    """Drop zero-signal request lines from the gunicorn/werkzeug access log.

    The access log prints one line per HTTP request, so static bundle assets,
    health-check pings, and 304 conditional revalidations bury the lines that
    actually matter. This filter drops those while always keeping 4xx/5xx (so
    failures still surface) and every real API/page hit. Matching is on the
    rendered message so it works for both gunicorn (deployed) and werkzeug
    (dev), whose access formats differ.
    """

    _LINE_RE = re.compile(r'"[A-Z]+ (?P<path>\S+) [^"]*" (?P<status>\d{3})')
    _NOISE_PREFIXES = ("/assets/", "/healthz")
    _NOISE_EXACT = {"/favicon.ico", "/robots.txt", "/vite.svg", "/manifest.webmanifest"}

    def filter(self, record: logging.LogRecord) -> bool:
        m = self._LINE_RE.search(record.getMessage())
        if m is None:
            return True  # not an access line (e.g. a werkzeug warning) — keep
        status = m.group("status")
        if status[0] in ("4", "5"):
            return True  # always surface client/server errors
        if status == "304":
            return False  # conditional revalidation did no work
        path = m.group("path").split("?", 1)[0]
        if path in self._NOISE_EXACT or path.startswith(self._NOISE_PREFIXES):
            return False
        return True


def _configure_logging() -> None:
    """Install the plain formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    # Avoid duplicate handlers on reload (Flask's reloader re-imports this module).
    if any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, PlainFormatter)
        for h in root.handlers
    ):
        return
    # Replace any pre-existing stream handlers (e.g. Flask's default) so we
    # don't double-print every record under the reloader.
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler):
            root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(PlainFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Silence chatty third-party libraries:
    # - httpx logs every HTTP request at INFO (one line per bucket read);
    #   bumping to WARNING keeps real failures, drops the per-request noise.
    # - huggingface_hub._login prints a benign HF_TOKEN-already-set warning
    #   on every fresh login() call when the env var is present.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub._login").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # Drop zero-signal access-log noise (static assets, health pings, 304s)
    # from both the gunicorn (deployed) and werkzeug (dev) request loggers.
    access_filter = _AccessLogFilter()
    for name in ("gunicorn.access", "werkzeug"):
        logging.getLogger(name).addFilter(access_filter)


_configure_logging()
logger = logging.getLogger("inspector")

if auth_service.is_dev_mode():
    logger.warning(
        "INSPECTOR_DEV_MODE=1 — synthetic dev user active, OAuth bypassed. "
        "Default role 'owner'; flip via the in-app role switcher or the "
        "'inspector_dev_role' cookie."
    )

_HERE = Path(__file__).parent.resolve()
FRONTEND_DIST = _HERE / "frontend" / "dist"


# Single-process invariant: state_store, per-slug threading.Lock, signed-
# cookie session verification, and the role cache all assume one worker.
# Boot fails if any multi-worker signal is set.
def _assert_single_worker() -> None:
    """Refuse to boot under any multi-worker config.

    Three independent signals are sniffed at import time because gunicorn's
    `-w` flag isn't on the env at fork time:

    1. ``GUNICORN_CMD_ARGS`` / ``GUNICORN_WORKERS`` / ``WEB_CONCURRENCY`` env
       vars — the standard gunicorn-recognised env knobs.
    2. ``sys.argv`` of the loader process — catches `gunicorn -w 2 ...`.
    3. (Future) a post-fork hook inside gunicorn — not currently wired.

    Any signal of >1 worker → loud RuntimeError.
    """
    suspects = [
        os.environ.get("GUNICORN_WORKERS"),
        os.environ.get("WEB_CONCURRENCY"),
        os.environ.get("GUNICORN_CMD_ARGS"),
    ]
    for raw in suspects:
        if not raw:
            continue
        for token in raw.replace("=", " ").split():
            if token.isdigit() and int(token) > 1:
                raise RuntimeError(f"Inspector requires a single worker (saw {token!r} via env).")
    argv = list(sys.argv)
    for i, a in enumerate(argv):
        if a in ("-w", "--workers") and i + 1 < len(argv):
            try:
                n = int(argv[i + 1])
            except ValueError:
                continue
            if n > 1:
                raise RuntimeError(f"Inspector requires -w 1 (got -w {n}).")
        elif a.startswith("--workers="):
            try:
                n = int(a.split("=", 1)[1])
            except ValueError:
                continue
            if n > 1:
                raise RuntimeError(f"Inspector requires --workers=1 (got {a}).")


_assert_single_worker()

# The dev-mode OAuth bypass serves every visitor as a synthetic owner; abort
# loudly if it is configured on a deployed (behind-proxy) Space.
auth_service.assert_dev_mode_safe()

# Flask's built-in static handler serves everything under FRONTEND_DIST at
# the site root (`/assets/<hash>.js`, `/fonts/DigitalKhattV2.otf`, …). The
# `/` route below handles index.html explicitly.
app = Flask(__name__, static_folder=str(FRONTEND_DIST), static_url_path="")

# Gzip (and brotli when the client advertises it) every JSON response over
# ~500 bytes. Pre-gzipped octet-stream bodies (Timestamps shards) fall outside
# the default MIME allow-list, so they're left untouched — no double-compress.
Compress(app)

# Behind HF Spaces' TLS-terminating proxy the X-Forwarded-* headers carry
# the real scheme/host/client; ProxyFix tells werkzeug to trust one hop so
# request.url_root reflects the public https URL (load-bearing for the
# OAuth redirect_uri). Gated by env so local dev / docker-compose runs
# without proxy headers are unaffected.
_behind_proxy = os.environ.get("INSPECTOR_BEHIND_PROXY") == "1"
if _behind_proxy:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    # One trusted hop is right for the direct *.hf.space host but one short for
    # a custom domain, where the nearest X-Forwarded-Proto is HF's internal
    # plaintext leg. Pin the scheme so the OAuth redirect_uri and the Secure
    # flag on our cross-site cookies stay correct on both hostnames.
    app.wsgi_app = ForceHttpsScheme(app.wsgi_app)

# Flask's secret key still signs anything that touches the session, but the
# OAuth state no longer lives there — it's held server-side in a per-process
# cache (see services/auth.py) because on HF Spaces the app runs in a
# cross-site huggingface.co iframe where the third-party session cookie is
# dropped by Safari, which surfaced as MismatchingStateError on the callback.
# Our identity cookie is separate and signed with the same secret.
try:
    app.secret_key = get_session_secret()
    app.config["MAX_CONTENT_LENGTH"] = SAMPLES_MAX_UPLOAD_BYTES
except MissingSecret as e:
    # Local dev / test paths may not have the secret seeded. Anyone hitting
    # an OAuth route gets a 503 from auth_service.is_oauth_configured().
    logger.warning("INSPECTOR_SESSION_SECRET unavailable: %s", e)

# Register the HF OAuth provider with Authlib so the auth routes can
# resolve oauth.huggingface at request time.
auth_service.init_oauth(app)

register_blueprints(app)


# ---------------------------------------------------------------------------
# Visitor analytics (Tier 2/3) — anon vs signed-in traffic counters.
# Gated by INSPECTOR_VISITOR_ANALYTICS=1 (prod Dockerfile sets it) so dev /
# tests have zero behavior change. Classification is HMAC-only (no DB) and
# counting is in-memory; the hourly flush daemon (wired in _boot_substrate)
# is the only thing that touches the bucket.
# ---------------------------------------------------------------------------

_ANON_SKIP_PREFIXES = ("/assets/", "/fonts/", "/api/webhooks/")
_ANON_SKIP_PATHS = {"/healthz", "/favicon.ico"}


def _visitor_analytics_on() -> bool:
    return os.environ.get("INSPECTOR_VISITOR_ANALYTICS") == "1"


@app.before_request
def _count_visitor():
    if not _visitor_analytics_on():
        return
    p = request.path
    if p in _ANON_SKIP_PATHS or p.startswith(_ANON_SKIP_PREFIXES):
        return
    cookie = request.cookies.get(auth_service.SESSION_COOKIE_NAME, "")
    payload = auth_service.decode_session(cookie) if cookie else None
    if payload is not None:
        visitor_analytics.record_hit(payload)
        return
    anon_id = request.cookies.get(ANON_COOKIE_NAME)
    visitor_analytics.record_hit(None, anon_id=anon_id)
    if not anon_id:
        g.set_anon_cookie = True  # mint one in _set_anon_cookie


@app.after_request
def _set_anon_cookie(resp):
    if getattr(g, "set_anon_cookie", False):
        resp.set_cookie(
            ANON_COOKIE_NAME,
            uuid.uuid4().hex,
            max_age=ANON_COOKIE_MAX_AGE,
            httponly=True,
            secure=_behind_proxy,
            samesite="None" if _behind_proxy else "Lax",
            path="/",
        )
    return resp


# ---------------------------------------------------------------------------
# SQLite substrate: boot on import so both `python3 inspector/app.py`
# and `gunicorn inspector.app:app` follow the same path. Errors degrade
# gracefully (503 on /healthz, but app still importable) — contributors get
# clear feedback rather than a hard crash.
# ---------------------------------------------------------------------------


def _boot_substrate() -> None:
    """Boot the SQLite substrate (the source of truth), then the idempotent
    boot-scan + opt-in daemons.

    Hard cut: the 6 legacy bucket JSON stores are no longer hydrated into
    memory — the DB pulled from the bucket IS the source of truth. Per-reciter
    content under ``reciters/<slug>/`` stays JSON-on-bucket and is read on demand.

    Boot order (matters): pull → migrate FIRST (a repo read before migration
    would hit an empty/old schema), THEN the boot-scan (which reads/writes the
    DB-backed state), THEN daemons (which call ``state.transition``). A failure
    in deployed mode (``INSPECTOR_BUCKET_MOUNT`` set) leaves the app importable
    with ``db_open:false`` so ``/healthz`` returns 503 — never a hard import
    crash; locally a real init error surfaces loudly.

    Tests skip this entirely and manage their own DB via the ``sqlite_db``
    fixture.
    """
    if "pytest" in sys.modules:
        return

    from services import db as _db
    from services.db import sync as _sync

    # Read-only escape hatch: INSPECTOR_DB_SYNC=0 disarms the per-commit bucket
    # upload so a local process can read a bucket (e.g. prod) without any write
    # ever syncing back. Boot still pulls the DB; nothing is ever pushed.
    if os.environ.get("INSPECTOR_DB_SYNC") == "0":
        _sync.set_sync_enabled(False)
        logger.info("db substrate: bucket write-back DISABLED (read-only mode)")

    deployed = bool(os.environ.get("INSPECTOR_BUCKET_MOUNT"))
    try:
        _sync.pull()  # bucket DB → local path (fresh init if the bucket has none)
        # Migration 28 is a guarded data cutover, not just DDL. Reconstruct its
        # exact native-target map from the active v12 shards before the SQL
        # runner checks that every legacy report is covered.
        from services.ts_reports.legacy_target_migration import (
            assert_native_report_schema,
            prepare_native_report_map,
        )

        prepare_native_report_map(_db.get_writer())
        ver = _db.init_db()  # open writer + run migrations (fail-fast)
        assert_native_report_schema(_db.get_writer())
        logger.info("db substrate: ready at schema v%s", ver)
    except Exception:  # noqa: BLE001
        logger.exception("db substrate init failed; /healthz will report db_open:false")
        if not deployed:
            raise
        return

    # Boot-scan: idempotent ``alignment_completed`` catch-up for ``reciters/<slug>/``
    # dirs stuck in AWAITING_ALIGNMENT. Wrapped in ``deferred_sync`` so its loop
    # of N transitions produces ONE coalesced bucket upload, not N (avoids a
    # boot-time upload storm / cross-container CAS thrash).
    try:
        with _sync.deferred_sync():
            auto_detect_service.hydrate_initial_seen()
    except Exception as e:  # noqa: BLE001
        logger.warning("auto_detect hydrate_initial_seen failed: %s", e)

    # 60s polling reconciler — opt-in (dev environments don't want a loop).
    if os.environ.get("INSPECTOR_AUTO_DETECT") == "1":
        try:
            interval = int(os.environ.get("INSPECTOR_AUTO_DETECT_INTERVAL_S", "60"))
            auto_detect_service.start_background_loop(interval_seconds=interval)
            logger.info("auto_detect: background loop scheduled (interval=%ss)", interval)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_detect background loop wiring failed: %s", e)

    # Visitor-analytics flush: hourly drain of in-memory traffic counters into
    # visitor_daily (one bucket upload/hour). Opt-in via
    # ``INSPECTOR_VISITOR_ANALYTICS=1`` (prod Dockerfile sets it).
    if os.environ.get("INSPECTOR_VISITOR_ANALYTICS") == "1":
        try:
            visitor_analytics.start_flush_daemon()
            logger.info("visitor analytics: hourly flush daemon started")
        except Exception as e:  # noqa: BLE001
            logger.warning("visitor analytics wiring failed: %s", e)

    # Release-job poll worker: 120 s background scan over running HF Jobs so
    # the DB-side completion handler fires even if the job's webhook callback
    # didn't reach us (firewall, dev localhost, restart window, or an automated
    # launch with no public webhook base). ``register_poll_handlers`` wires every
    # poll-completable kind (ts + hf_publish + hf_publish_batch + refresh_catalog)
    # from one list; cut_release is webhook-only (needs the full members payload)
    # so it's excluded there. Opt-out via ``INSPECTOR_RELEASE_POLL=0``.
    if os.environ.get("INSPECTOR_RELEASE_POLL", "1") == "1":
        try:
            from services.admin.jobs import base as _jobs_base

            _jobs_base.register_poll_handlers()
            _jobs_base.start_poll_worker()
            logger.info("release-job poll worker started")
        except Exception as e:  # noqa: BLE001
            logger.warning("release-job poll wiring failed: %s", e)

    # Native align pipeline: resume any pending/running align run's worker thread
    # (a ``running`` row after a restart means the previous process died
    # mid-stage; every stage resumes from its staged files). Opt-in via
    # ``INSPECTOR_ALIGN_PIPELINE=1`` so a local checkout never drives the Spaces.
    if os.environ.get("INSPECTOR_ALIGN_PIPELINE") == "1":
        try:
            from services.admin.align_pipeline import runner as _align_runner

            _align_runner.resume_active()
            logger.info("align pipeline: workers resumed")
        except Exception as e:  # noqa: BLE001
            logger.warning("align pipeline wiring failed: %s", e)

    # Release-automation reconciler: a ~60 s loop that fires the release jobs on
    # the owner's schedule/rules (see services/admin/automation/). Opt-in via
    # ``INSPECTOR_AUTOMATIONS=1`` (prod Dockerfile sets it; off in dev) so a local
    # checkout never spends HF Job compute on its own.
    if os.environ.get("INSPECTOR_AUTOMATIONS") == "1":
        try:
            from services.admin.automation import engine as _automation_engine

            _automation_engine.start_background_loop()
            logger.info("automation reconciler started")
        except Exception as e:  # noqa: BLE001
            logger.warning("automation reconciler wiring failed: %s", e)

    # Email-digest flush: a ~60 s sweep over the email_digest buffer that sends
    # one batched email per (recipient, event) window for the two high-volume
    # events (recitation_published / timestamps_regenerated). A tick with nothing
    # due does zero writes (no bucket upload). Opt out via
    # ``INSPECTOR_EMAIL_DIGEST_FLUSH=0``.
    if os.environ.get("INSPECTOR_EMAIL_DIGEST_FLUSH", "1") == "1":
        try:
            from services.email import digest as _email_digest

            _email_digest.start_flush_daemon()
            logger.info("email digest flush daemon started")
        except Exception as e:  # noqa: BLE001
            logger.warning("email digest flush wiring failed: %s", e)


_boot_substrate()


# ---------------------------------------------------------------------------
# Error handlers — preserve {error: str} envelope across all routes
# ---------------------------------------------------------------------------


@app.errorhandler(HTTPException)
def _handle_http_exception(e: HTTPException):
    """Return the canonical ``{error: <description>}`` envelope with the HTTP status."""
    return jsonify({"error": e.description}), e.code or 500


@app.errorhandler(UnknownReciter)
def _handle_unknown_reciter(e: UnknownReciter):
    return jsonify(error_body(f"unknown reciter: {e}", code=Codes.UNKNOWN_RECITER)), 404


@app.errorhandler(InvalidTransition)
def _handle_invalid_transition(e: InvalidTransition):
    return (
        jsonify(
            error_body(
                str(e),
                code=getattr(e, "code", None) or Codes.STATE_PRECONDITION,
                context=getattr(e, "context", None),
                details=getattr(e, "details", None),
            )
        ),
        400,
    )


@app.errorhandler(NotAuthorizedForTransition)
def _handle_not_authorized_transition(e: NotAuthorizedForTransition):
    return (
        jsonify(
            error_body(
                str(e),
                code=getattr(e, "code", None) or Codes.NOT_AUTHORIZED,
                context=getattr(e, "context", None),
            )
        ),
        403,
    )


@app.errorhandler(StorageReadOnly)
def _handle_read_only(e: StorageReadOnly):
    """A write was attempted while INSPECTOR_READ_ONLY=1 (e.g. `launch --mode
    prod`). Fail clean with 403 instead of a generic 500 so the FE can say so."""
    return (
        jsonify(
            error_body("This instance is read-only; changes aren't saved.", code=Codes.READ_ONLY)
        ),
        403,
    )


@app.errorhandler(RiwayahMismatch)
def _handle_riwayah_mismatch(e: RiwayahMismatch):
    """The catalog row and ``detailed.json`` name different editions.

    Registered app-wide rather than per route: every Segments read, save, undo
    and validate resolves the delivery's edition, and a delivery whose two
    records disagree is genuinely unserveable — it must read as a conflict the
    FE can explain, not as a 500 the FE cannot tell from a crash.
    """
    return jsonify(error_body(str(e), code=Codes.RIWAYAH_MISMATCH)), 409


@app.errorhandler(UnsupportedRiwayah)
def _handle_unsupported_riwayah(e: UnsupportedRiwayah):
    return jsonify(error_body(str(e), code=Codes.UNSUPPORTED_RIWAYAH)), 409


@app.errorhandler(RefNotInEdition)
def _handle_ref_not_in_edition(e: RefNotInEdition):
    """A saved ``matched_ref`` that names no word of the delivery's edition.

    The ref comes from the client, so this is a 400 — and a specific one:
    Warsh and Qalun renumber 50 surahs, so a ref that is perfectly valid in
    Hafs can name nothing here, and "internal server error" would send the
    reviewer looking for a fault instead of at their ref.
    """
    return jsonify(error_body(str(e), code=Codes.REF_NOT_IN_EDITION)), 400


@app.errorhandler(EditionsUnavailable)
def _handle_editions_unavailable(e: EditionsUnavailable):
    """A non-Hafs delivery on a build without ``qua_domain``.

    A documented degraded state, not a fault: the image was built without the
    deploy key, or ``INSPECTOR_MULTI_RIWAYAH=0``. 503 says "this deployment
    cannot serve it" rather than "something broke".
    """
    return jsonify(error_body(str(e), code=Codes.EDITIONS_UNAVAILABLE)), 503


@app.errorhandler(Exception)
def _handle_unexpected_exception(e: Exception):
    """Log uncaught exceptions and return a generic envelope (don't leak internals)."""
    logger.exception("unhandled exception: %s", e)
    return jsonify({"error": "internal server error"}), 500


# ---------------------------------------------------------------------------
# Static / index routes
# ---------------------------------------------------------------------------

_BUILD_HINT = "Frontend not built. Run:\n  cd inspector/frontend && npm ci && npm run build\n"


@app.route("/")
def index():
    """Serve the Vite-built SPA shell."""
    if not (FRONTEND_DIST / "index.html").exists():
        return _BUILD_HINT, 500, {"Content-Type": "text/plain"}
    return send_from_directory(str(FRONTEND_DIST), "index.html")


# ---------------------------------------------------------------------------
# Cross-tab routes (not under any single tab's namespace)
# ---------------------------------------------------------------------------


@app.route("/api/surah-info")
def get_surah_info():
    """Return lightweight surah metadata."""
    # Pure Quran-structure constants — never change without a redeploy.
    return orjson_response(
        load_surah_info_lite(),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alignment Inspector Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to run on")
    args = parser.parse_args()

    if not (FRONTEND_DIST / "index.html").exists():
        logger.warning(
            "%s not found. Run `cd inspector/frontend && npm ci && npm run build` "
            "before visiting /. For frontend dev: `cd inspector/frontend && npm run dev` "
            "and visit http://localhost:5173 (Vite proxies /api + /audio to this Flask).",
            FRONTEND_DIST / "index.html",
        )

    # Phonemizer is no longer used by the validate runtime path; the phonemic
    # side of boundary_adj is captured at backfill / extraction time and
    # persisted as ``is_boundary_adj`` on every segment. The remaining
    # consumer is ``scripts/backfills/backfill_boundary_adj.py`` (offline)
    # which imports lazily on demand.

    # Timestamp data loads lazily on first request now (per-reciter cache in
    # services/data_loader.py). The earlier eager preload pinned ~22 MB *
    # 300 reciters at startup, which is unviable at deployed scale.

    # Vite owns frontend file-watching (HMR in dev; rebuild on npm run build).
    # The Werkzeug reloader is DELIBERATELY OFF (even in debug): it runs two
    # processes (supervisor + worker), each of which imports this module and
    # runs `_boot_substrate()` → both open the single SQLite writer connection.
    # That violates the single-worker invariant; on Windows the second boot's
    # `sync.pull()` `os.replace` is denied (the other process holds the DB),
    # crashing startup with WinError 5 (on Linux it silently races). Python
    # changes therefore need a manual restart; debug still gives rich error pages.
    debug = os.environ.get(FLASK_ENV_VAR) == FLASK_DEV_VALUE
    logger.info("Starting server at http://localhost:%d (debug=%s, reloader=off)", args.port, debug)
    app.run(host=SERVER_HOST, port=args.port, debug=debug, use_reloader=False)
