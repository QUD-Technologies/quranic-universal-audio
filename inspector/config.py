import os
import sys
from pathlib import Path

# Repo root (inspector/ is one level below)
_REPO = Path(__file__).resolve().parent.parent

# The repo root carries ``qua_shared``. app.py puts it on sys.path at startup,
# but config.py can be imported before that — by a script, or by a test
# collecting from inspector/ — so make the import work either way.
sys.path.insert(0, str(_REPO))
from qua_shared.riwayat import SUPPORTED_RIWAYAT as _INSPECTOR_RIWAYAT  # noqa: E402

#: SDK slugs, in product order. Defined once, in qua_shared.riwayat.
_SDK_RIWAYAT = tuple(_INSPECTOR_RIWAYAT.values())

# Data root — holds only bundled reference data (surah_info, qpc_hafs,
# digital_khatt, phoneme_sub_costs, .audio_meta) in deployed mode. All
# per-reciter content lives in the bucket at INSPECTOR_BUCKET_MOUNT.
DATA_DIR = Path(os.environ.get("INSPECTOR_DATA_DIR", str(_REPO / "data"))).resolve()

SURAH_INFO_PATH = DATA_DIR / "surah_info.json"

# Legacy filesystem constant — only consulted by ``services/history_query.py``
# tests that monkeypatch a tmp_path in. Production reads edit_history through
# ``data_dir`` (bucket-resolved).
RECITATION_SEGMENTS_PATH = DATA_DIR / "recitation_segments"

# Dev-only: accordion-guide example flagging tool. Local scratch JSONL queue of
# edit-history ops the maintainer flags while browsing the History panel, for
# later conversion into guide examples. Lives under the gitignored ``.local/`` —
# never the bucket, never git. Paired with the dev-only routes in
# ``routes/auth/dev.py`` + ``services/segments/guide_flags.py``; absent on the
# deployed Space (the routes 404 when dev mode is off). See
# ``docs/reference/accordion-guides.md``.
GUIDE_FLAGS_PATH = Path(
    os.environ.get(
        "INSPECTOR_GUIDE_FLAGS_PATH",
        str(_REPO / ".local" / "guide_flags" / "flags.jsonl"),
    )
).resolve()

# Bundled linguistic data (qpc_hafs, digital_khatt, phoneme_sub_costs) lives in
# the repo's ``data/`` dir alongside ``surah_info.json``. Override the base dir
# via INSPECTOR_QUA_DATA_PATH for standalone / Docker deployments.
_QUA_DATA_OVERRIDE = os.getenv("INSPECTOR_QUA_DATA_PATH")
_QUA_DATA = Path(_QUA_DATA_OVERRIDE) if _QUA_DATA_OVERRIDE else DATA_DIR
QPC_HAFS_PATH = _QUA_DATA / "qpc_hafs.json"
DK_SCRIPT_PATH = _QUA_DATA / "digital_khatt_v2_script.json"
PHONEME_SUB_COSTS_PATH = _QUA_DATA / "phoneme_sub_costs.json"

# Display settings
UNIFIED_DISPLAY_MAX_HEIGHT = 800  # px

# Animation settings
# Easing function for opacity transitions:
#   ease         — slow start/end, fast middle (default)
#   linear       — constant speed
#   ease-in      — slow start, fast end
#   ease-out     — fast start, slow end
#   ease-in-out  — slow both ends
#   none         — instant snap, no animation
ANIM_TRANSITION_EASING = "ease"
ANIM_HIGHLIGHT_COLOR = "#f0a500"  # gold — active word/char color (both views)
ANIM_WORD_TRANSITION_DURATION = 0.15  # seconds — word opacity transition
ANIM_CHAR_TRANSITION_DURATION = 0.02  # seconds — char opacity transition
ANIM_WORD_SPACING = "0.2em"  # gap between words in animation view
ANIM_LINE_HEIGHT = 2.0  # line-height for animation text
ANIM_FONT_SIZE = "44px"  # Arabic text size in animation view

# Analysis view settings
ANALYSIS_WORD_FONT_SIZE = "1.5rem"  # word row text size in analysis view
ANALYSIS_LETTER_FONT_SIZE = "1.75rem"  # letter sub-row text size in analysis view

# Segments tab settings
SEG_FONT_SIZE = "1.8rem"  # Arabic text size in segment cards
SEG_WORD_SPACING = "0.2em"  # gap between words in segment cards

# Auto-scroll animation mode for the segments list.
# Values: "none"   — instant (no animation)
#         "smooth" — native CSS smooth scroll on every target
#         "hybrid" — smooth only when the jump exceeds one viewport, instant otherwise
# Not a user preference — the same behavior ships for all deployments.
SEG_SCROLL_ANIM_MODE = "hybrid"

# Adjust (trim) mode settings
TRIM_PAD_LEFT = 15000  # ms padding before segment
TRIM_PAD_RIGHT = 15000  # ms padding after segment
TRIM_DIM_ALPHA = 0.4  # dimming opacity for padded regions

# Accordion context: which validation categories auto-expand context cards
# Values: "shown" (default open), "hidden" (default closed), "next_only" (open on nav)
ACCORDION_CONTEXT = {
    "failed": "shown",
    "missing_words": "shown",
    "low_confidence": "hidden",
    "boundary_adj": "hidden",
    "repetitions": "hidden",
    "cross_verse": "hidden",
    "muqattaat": "hidden",
    "qalqala": "hidden",
    "audio_bleeding": "shown",
    "unmarked_wasl": "next_only",
}

# Maintainer sample uploads (one audio + one JSON, multipart). Applied as the
# app-wide request body cap; every other request body is far smaller.
SAMPLES_MAX_UPLOAD_BYTES = 300 * 1024 * 1024

# HTTP / subprocess timeouts (seconds)
FFMPEG_TIMEOUT = 15
FFMPEG_FULL_TIMEOUT = 300

# Audio processing
MIN_SEG_PEAK_BUCKETS = 10
MIN_FULL_PEAK_BUCKETS = 100
PEAKS_BUCKETS_PER_SEC = 30  # target peak density for segment-level peaks

# Validation thresholds
#
# ``LOW_CONFIDENCE_THRESHOLD`` is the SINGLE source of truth for the badge
# cutoff: classifier flags segments with confidence < this value as
# ``low_confidence`` (services/validation/classifier.py), the
# mark-ready gate counts items below this value
# (services/state/state.py::_h_marked_ready), and the FE accordion's
# slider default + form's blocking-counts panel derive from the
# percentage form below (shipped via /api/seg/data as
# ``low_conf_default_threshold``). Bump this and both layers agree.
LOW_CONFIDENCE_THRESHOLD = 0.80
# Percentage scale (0–99 int) the FE slider works in. Derived — never
# edit this independently; the assertion below guards against drift.
LOW_CONF_DEFAULT_THRESHOLD = int(round(LOW_CONFIDENCE_THRESHOLD * 100))
assert 50 <= LOW_CONF_DEFAULT_THRESHOLD <= 99, (
    f"LOW_CONFIDENCE_THRESHOLD={LOW_CONFIDENCE_THRESHOLD!r} produced an "
    f"out-of-range slider default {LOW_CONF_DEFAULT_THRESHOLD}; the FE "
    f"slider expects 50–99 (see frontend/.../config-loader.ts)."
)
# Minimum number of pipeline-stripped basmalas required before we flag the
# remaining (non-stripped) chapters as "missed Basmala" candidates. Below this
# count the reciter is treated as one who doesn't recite inter-chapter
# basmalas, and the augmentation is suppressed to avoid accordion noise.
MISSED_BASMALA_FLAG_MIN_DELETED = int(os.getenv("MISSED_BASMALA_FLAG_MIN_DELETED", "10"))
# "Show everything below perfect" tier — used to populate detail lists (not count badges).
# Distinct from LOW_CONFIDENCE_THRESHOLD (count badge cutoff) and LOW_CONFIDENCE_RED (red highlight).
LOW_CONFIDENCE_DETAIL_THRESHOLD = 1.0

# Per-edition low-confidence cutoff, keyed on SDK riwayah slug.
#
# Confidence is a DP-matching score against the Hafs reference text, and the
# non-Hafs editions are aligned through that same Hafs proxy — so their scores
# are on the same scale and every edition ships at the Hafs value. They get
# their own knob anyway because the first real Warsh delivery may show a
# systematically different score distribution (a different reciting style, or
# the projection's split/join words scoring lower), and re-tuning one edition
# must not move the badge cutoff under the 37 published Hafs reciters.
#
# Override per edition with e.g. ``INSPECTOR_LOW_CONF_WARSH=0.72``.
LOW_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    slug: float(os.getenv(f"INSPECTOR_LOW_CONF_{slug.upper()}", str(LOW_CONFIDENCE_THRESHOLD)))
    for slug in _SDK_RIWAYAT
}
METADATA_PEEK_BYTES = 512

# Statistics histogram defaults
PAUSE_HIST_BIN_MS = 50
PAUSE_HIST_MAX_MS = 3000
SEG_DUR_HIST_BIN_MS = 500
SEG_DUR_HIST_MAX_MS = 15000

# Cache headers
AUDIO_CACHE_MAX_AGE = 31_536_000

# Confidence thresholds
LOW_CONFIDENCE_RED = 0.60  # below this = red highlight ("below_60" stat)

# Peaks (ffmpeg) — waveform peak extraction defaults (services/peaks.py)
PEAKS_FFMPEG_SAMPLE_RATE = 8000  # Hz — ffmpeg resample target for peak computation
PEAKS_PCM_NORMALIZER = 32768.0  # divisor that maps int16 PCM → [-1, 1] float
PEAKS_WORKER_COUNT = 8  # ThreadPoolExecutor workers for parallel peak compute
# v1 used integer block size (`num_samples // num_buckets`) and dropped the
# trailing samples that didn't fit, leaving the peaks array advertising
# `duration_ms` larger than what it actually covered — a 0.25% multiplicative
# time→peak drift visible as misregistered silences on long chapters
# (~2 s offset 15 min in, ~17 s offset at end-of-file for a ~115 min surah).
# v2 uses a float stride so every sample is bucketed; the read path treats a
# missing/mismatched version as a cache miss (falls through to per-segment
# ffmpeg — there is no runtime re-bake).
#
# v3 introduces the slim packed shape (int8-quantized, decimated, gzipped) at
# ``reciters/<slug>/peaks/<chapter>.json.gz``. See ``services/audio/peaks_slim.py``
# for the format. Pre-v3 ``.json`` files are packed by
# ``scripts/backfill_peaks_slim.py`` and preserved as ``.json.bak``. The
# runtime reader (``audio_fetch.read_prefetched_peaks`` → ``unpack_slim_envelope``)
# returns the int8 envelope verbatim; the FE decodes the b64 once into an
# ``Int8Array`` (no server-side float inflation).
PEAKS_SCHEMA_VERSION = 3

# Server defaults (app.py)
DEFAULT_PORT = 5000  # Flask --port default
FLASK_ENV_VAR = "FLASK_ENV"  # environment variable name for Flask env
FLASK_DEV_VALUE = "development"  # value that triggers debug/reloader mode
# Bind host — override with INSPECTOR_HOST for non-local deployments.
SERVER_HOST = os.environ.get("INSPECTOR_HOST", "0.0.0.0")

# Timestamps validation (routes/timestamps.py)
TS_RANDOM_MAX_RETRIES = 10  # max attempts to find a non-empty verse in /ts/random
TS_BOUNDARY_TOLERANCE_MS = 500  # default boundary tolerance when not in result metadata
# A timing report flags a boundary (onset/offset) as off. When a re-stamp shifts
# that flagged boundary by more than this, the open report is marked stale for
# owner re-check (ts_reports staleness; services/ts_reports/ts_target_snapshot.py).
TS_REPORT_BOUNDARY_STALE_MS = int(os.environ.get("TS_REPORT_BOUNDARY_STALE_MS", "100"))
# Sort-order weight for missing-word issues: one missing word adds this many ms to diff_ms.
# This is NOT a unit conversion — it is purely a sort-order heuristic.
MISSING_WORD_DIFF_MS_WEIGHT = 1000

# Statistics histogram shape constants (services/stats.py)
WORDS_PER_SEG_HIST_MAX = 15  # upper bound for words-per-segment histogram
SEGS_PER_VERSE_HIST_MAX = 8  # upper bound for segs-per-verse histogram
CONF_HIST_BIN_SIZE = 5  # bin width (percentage points) for confidence histogram

# Auto-split (services/auto_split.py): MFA Space + timeout. Cross-verse
# accordion's "Auto Split" routes here to compute the verse-boundary split
# time. Always the dev Space — interactive call volume is low. Override
# via INSPECTOR_MFA_SPACE_URL. On any failure or timeout the FE silently
# falls back to a midpoint cursor.
MFA_SPACE_URL = os.environ.get(
    "INSPECTOR_MFA_SPACE_URL", "https://hetchyy-quran-phoneme-mfa-dev.hf.space"
)
AUTO_SPLIT_MFA_TIMEOUT = 15

# Admin dashboard — identity recency + visitor analytics
# How stale last_entry_at must be before /api/me writes again (debounce so a
# browsing session is at most one write per user per window, not per page load).
ENTRY_DEBOUNCE_SECONDS = int(os.getenv("INSPECTOR_ENTRY_DEBOUNCE_SECONDS", "900"))
# Visitor-counter flush cadence (in-memory → visitor_daily). One DB txn per
# flush = one bucket upload per flush; never per request.
VISITOR_FLUSH_INTERVAL_SECONDS = int(os.getenv("INSPECTOR_VISITOR_FLUSH_INTERVAL_SECONDS", "3600"))
# Opaque first-party cookie for approximate unique-anonymous counting (random
# uuid4; NO fingerprinting, NO PII). Set only for anonymous callers.
ANON_COOKIE_NAME = "inspector_anon"
ANON_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

# Release automations (services/admin/automation/) — opt-in reconciler daemon.
# The daemon is gated by INSPECTOR_AUTOMATIONS=1 (set in the prod Dockerfile);
# this is just its tick cadence. Per-automation enable/schedule/guard live in the
# owner's AutomationConfig blob, not here.
AUTOMATION_INTERVAL_SECONDS = int(os.getenv("INSPECTOR_AUTOMATIONS_INTERVAL_S", "60"))
# Public https root the daemon threads into job completion webhooks (it has no
# request context for request.url_root). Required for automated GH cuts — the
# cut job is webhook-only (no poll fallback). Empty in dev → automated cuts can't
# complete cleanly, which is fine since automations are off in dev.
AUTOMATION_PUBLIC_BASE_URL = os.environ.get("INSPECTOR_PUBLIC_BASE_URL", "").strip()

# Email notifications (inspector/email) — Brevo transactional REST API over HTTPS
# (HF Spaces block outbound SMTP on every port, so smtplib can't deliver). The
# BREVO_API_KEY secret authenticates; when it's unset (local dev) the sender logs
# the rendered email instead of dispatching, so the flow is exercisable without it.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
# Display name on the From header.
EMAIL_FROM_NAME = os.environ.get("INSPECTOR_EMAIL_FROM_NAME", "Quranic Universal Audio")
# From address — MUST be a Brevo-verified sender. Defaults to the GMAIL account
# (already a verified single-sender) unless overridden.
EMAIL_FROM_ADDRESS = (
    os.environ.get("INSPECTOR_EMAIL_FROM_ADDRESS", "").strip()
    or os.environ.get("GMAIL", "").strip()
)
# User-facing "visit the site" link in every email — the public site.
EMAIL_SITE_URL = os.environ.get("INSPECTOR_EMAIL_SITE_URL", "https://universal-audio.qud.dev")
# The GitHub releases page linked from release emails.
EMAIL_GH_RELEASES_URL = os.environ.get(
    "INSPECTOR_EMAIL_GH_RELEASES_URL",
    "https://github.com/QUD-Technologies/quranic-universal-audio/releases",
)
# Base URL for functional links (the manage deep-link + unsubscribe endpoint) —
# the direct app host. Reuses INSPECTOR_PUBLIC_BASE_URL; localhost in dev.
EMAIL_APP_BASE_URL = (
    os.environ.get("INSPECTOR_PUBLIC_BASE_URL", "").strip() or "http://localhost:5000"
)

# Email digest — per-event batching window. The two high-volume events
# (recitation_published, timestamps_regenerated) buffer per recipient and flush
# as ONE batched email per event-kind once the window (opened by the earliest
# buffered event) elapses; bounds a burst to at most one email per event per
# window. Not cross-event — the two never merge. The flush daemon sweeps the
# buffer on this interval; opt out with INSPECTOR_EMAIL_DIGEST_FLUSH=0.
EMAIL_DIGEST_WINDOW_MINUTES = int(os.getenv("INSPECTOR_EMAIL_DIGEST_WINDOW_MINUTES", "60"))
EMAIL_DIGEST_FLUSH_INTERVAL_SECONDS = int(
    os.getenv("INSPECTOR_EMAIL_DIGEST_FLUSH_INTERVAL_S", "60")
)

# Audio MIME types (shared between app.py and audio_proxy)
AUDIO_MIME_TYPES = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
}


# ---------------------------------------------------------------------------
# Multi-riwayah
# ---------------------------------------------------------------------------
# Kill-switch for the non-Hafs editions. Default on; forced off in practice
# whenever ``qua_domain`` cannot be imported (see
# ``services/reference/editions.py``). Set to 0 to disable without rebuilding
# the image: an HF Space *variable* change plus a restart is a 60-second
# rollback, where a Docker rebuild is ten minutes.
#
# Disabling does NOT make non-Hafs deliveries render as Hafs — they fail
# loudly. Silently substituting Hafs coordinates under another edition's
# script would let a reviewer save wrong refs.
MULTI_RIWAYAH_ENABLED = os.getenv("INSPECTOR_MULTI_RIWAYAH", "1") not in ("0", "false", "False")
