"""Shared bootstrap for inspector-admin scripts.

Everything every admin script needs in one place:

  setup(args, *, need_db=True, need_actor=True) → BootstrapCtx
    - resolves repo root, sets sys.path so `services.*` + `qua_shared.*` import
    - loads HF_TOKEN from .env if missing in env
    - picks bucket repo from --prod (refuses prod for --yes-prod-only ops without the flag)
    - sets a script-private INSPECTOR_DB_PATH so we don't collide with a
      running local dev Inspector (same trick the existing launch_ts_job.py used)
    - if need_db: pulls the SQLite from the bucket + initialises the writer
    - if need_actor: constructs a real Actor from INSPECTOR_DEV_OWNER_* env

  prod_safe_setup(args, **setup_kw) → context manager yielding BootstrapCtx
    - THE required wrapper for any PROD bucket-DB mutation. Pauses the deployed
      Space (single-writer), pulls fresh, yields ctx for the mutation, then
      ALWAYS resumes (Space re-pulls the corrected DB on boot). A bare setup()
      prod write is clobbered by the live Space's advancing db_seq — never do it.
    - dev: a plain setup() (already single-writer), no pause.

  add_common_args(parser, *, mutating=True)
    - adds --prod / --yes-prod / --dry-run

  after_write_banner(args)
    - prints the standing "deployed Space needs a restart" reminder

Pattern in each script:
    from _bootstrap import setup, add_common_args, after_write_banner
    p = argparse.ArgumentParser(...)
    add_common_args(p)
    a = p.parse_args()
    ctx = setup(a)
    # ... do the work via services/admin/* and state.transition ...
    if not a.dry_run:
        after_write_banner(a)
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


def _ensure_inspector_runtime() -> None:
    """Make inspector imports work under any interpreter (run at import time).

    The inspector chain uses ``datetime.UTC`` (3.11 alias). Polyfill it so the
    import works under a 3.10 interpreter that already has the deps; only
    re-exec under ``python3.11`` when the env is bare (no ``pydantic``), so
    plain ``python3 admin_*.py`` Just Works without the caller picking 3.11.
    Mirrors ``.local/extraction/_pyguard.py`` (separate repo → can't share).
    """
    import datetime as _dt
    if not hasattr(_dt, "UTC"):
        _dt.UTC = _dt.timezone.utc
    if sys.version_info[:2] >= (3, 11):
        return
    if importlib.util.find_spec("pydantic") is not None:
        return
    for minor in range(11, 20):
        exe = shutil.which(f"python3.{minor}")
        if exe:
            os.execv(exe, [exe, *sys.argv])
    raise SystemExit(
        "inspector-admin needs Python >= 3.11 with deps; none found on PATH "
        f"(running {sys.version_info[0]}.{sys.version_info[1]})."
    )


_ensure_inspector_runtime()

# Bucket id table mirrors inspector/services/storage/hf_bucket.py.
BUCKETS = {
    "dev": "QUD-Technologies/quranic-inspector-bucket-dev",
    "prod": "QUD-Technologies/quranic-inspector-bucket",
}

# Deployed prod Space that holds the SQLite writer (single-writer invariant).
PROD_SPACE_ID = os.environ.get("INSPECTOR_SPACE_ID", "QUD-Technologies/quranic-universal-audio")

# Set True only while inside a ``prod_safe_setup`` window (Space paused). ``setup``
# refuses a prod MUTATION when this is False, so a bare prod write can't slip
# through the clobber-prone path — every prod mutation must go via ``run`` /
# ``prod_safe_setup``.
_SAFE_WINDOW = False


def repo_root() -> Path:
    # .claude/skills/inspector-admin/scripts/<file>.py → up 4 = repo root
    return Path(__file__).resolve().parents[4]


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_env() -> None:
    """HF_TOKEN + INSPECTOR_DEV_OWNER_* from repo-root .env if missing."""
    env_file = repo_root() / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and not os.environ.get(k):
            os.environ[k] = v


def add_common_args(parser: argparse.ArgumentParser, *, mutating: bool = True) -> None:
    parser.add_argument("--prod", action="store_true",
                        help="target the prod bucket (default: dev)")
    if mutating:
        parser.add_argument("--yes-prod", action="store_true",
                            help="required for a prod mutating op")
        parser.add_argument("--dry-run", action="store_true",
                            help="run the handler but skip durable_transaction")


@dataclass
class BootstrapCtx:
    repo_root: Path
    bucket_id: str           # QUD-Technologies/quranic-inspector-bucket{,-dev}
    actor: Optional[object]  # qua_shared.schemas.config.audit.Actor when need_actor=True
    db_path: Path
    db_synced: bool


def setup(args: argparse.Namespace, *, need_db: bool = True,
          need_actor: bool = True,
          mutates: bool | None = None,
          safe_write: bool = False) -> BootstrapCtx:
    _ensure_utf8_stdout()

    rr = repo_root()
    for p in (str(rr), str(rr / "inspector")):
        if p not in sys.path:
            sys.path.insert(0, p)

    _load_env()

    bucket_kind = "prod" if getattr(args, "prod", False) else "dev"
    bucket_id = BUCKETS[bucket_kind]
    os.environ["INSPECTOR_BUCKET_REPO"] = bucket_id
    os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1" if bucket_kind == "prod" else "0"

    # Mutating-against-prod gate. ``mutates`` is the explicit signal — when
    # left None, fall back to "did the parser register a --yes-prod flag?"
    # which works for compile-time-known mutations. Subcommands whose write
    # intent depends on a runtime flag (e.g. admin_db exec --write) pass an
    # explicit ``mutates=`` and bypass the heuristic.
    is_mutation = (mutates if mutates is not None
                   else hasattr(args, "yes_prod"))
    if (bucket_kind == "prod" and is_mutation
            and not getattr(args, "yes_prod", False)
            and not getattr(args, "dry_run", False)):
        print("refusing to write to prod bucket without --yes-prod",
              file=sys.stderr)
        sys.exit(2)
    # Single-writer guard: an in-process bucket-DB write (``safe_write``) MUST
    # run inside a prod_safe_setup window (Space paused) or the live Space
    # clobbers the edit within minutes. ``run(..., safe_write=True)`` routes it
    # through that window automatically; a direct prod DB write here is a bug.
    # (Job launches set safe_write=False — they touch no DB in-process, so they
    # need --yes-prod but NOT the pause; pausing would also break --monitor.)
    if (bucket_kind == "prod" and safe_write
            and not getattr(args, "dry_run", False)
            and not _SAFE_WINDOW):
        print("refusing a direct prod DB write outside prod_safe_setup — route it "
              "through bs.run(..., safe_write=True) so the Space is paused "
              "(single-writer). See the skill's prod_safe_setup section.",
              file=sys.stderr)
        sys.exit(2)

    # Script-private SQLite path so we don't fight a running local Inspector
    # (Windows os.replace can't overwrite an open file).
    db_path = Path(tempfile.gettempdir()) / f"inspector_admin_{bucket_kind}.db"
    os.environ["INSPECTOR_DB_PATH"] = str(db_path)

    actor = None
    if need_actor:
        from qua_shared.schemas import Actor  # noqa: E402
        from qua_shared.schemas.config.access import Role  # noqa: E402
        hf_id = os.environ.get("INSPECTOR_DEV_OWNER_HF_ID", "").strip()
        login = os.environ.get("INSPECTOR_DEV_OWNER_LOGIN", "").strip()
        if not hf_id or not login:
            print("missing INSPECTOR_DEV_OWNER_HF_ID / _LOGIN in env or .env",
                  file=sys.stderr)
            sys.exit(3)
        actor = Actor(hf_user_id=hf_id, login_at_time=login, role=Role.OWNER)

    db_synced = False
    if need_db:
        from services import db as _db  # noqa: E402
        from services.db import sync as _db_sync  # noqa: E402
        db_synced = _db_sync.pull()
        _db.init_db()

    return BootstrapCtx(repo_root=rr, bucket_id=bucket_id, actor=actor,
                        db_path=db_path, db_synced=db_synced)


# Bucket advisory lock — serialises concurrent admin single-writer windows so
# two admin runs can't overlap their pause/edit/resume (which would let one's
# resume un-pause the Space mid-edit of the other). TTL'd so a crashed holder
# self-clears. Best-effort (read-check-write + read-back, not a true CAS) —
# enough to catch accidental concurrency, not adversarial contention.
_ADMIN_LOCK_PATH = "db/.admin_lock.json"
_ADMIN_LOCK_TTL_S = 1200


def _acquire_admin_lock(backend: object, nonce: str, login: str) -> None:
    existing = None
    try:
        if backend.exists(_ADMIN_LOCK_PATH):  # type: ignore[attr-defined]
            existing = backend.read_json(_ADMIN_LOCK_PATH)  # type: ignore[attr-defined]
    except Exception:
        existing = None
    if (
        isinstance(existing, dict)
        and existing.get("nonce") != nonce
        and float(existing.get("expires_at", 0)) > time.time()
    ):
        raise RuntimeError(
            f"admin lock held by '{existing.get('login')}' since "
            f"{existing.get('acquired_at')} (expires {existing.get('expires_at')}). "
            "Another inspector-admin prod write is in progress — aborting."
        )
    payload = {
        "nonce": nonce,
        "login": login,
        "acquired_at": time.time(),
        "expires_at": time.time() + _ADMIN_LOCK_TTL_S,
    }
    backend.write_json_atomic(_ADMIN_LOCK_PATH, payload)  # type: ignore[attr-defined]
    # Read-back: if a near-simultaneous writer overwrote us, we lost the race.
    try:
        back = backend.read_json(_ADMIN_LOCK_PATH)  # type: ignore[attr-defined]
    except Exception:
        back = payload
    if not (isinstance(back, dict) and back.get("nonce") == nonce):
        raise RuntimeError("admin lock race lost to a concurrent admin run — aborting.")


def _release_admin_lock(backend: object, nonce: str) -> None:
    try:
        if backend.exists(_ADMIN_LOCK_PATH):  # type: ignore[attr-defined]
            cur = backend.read_json(_ADMIN_LOCK_PATH)  # type: ignore[attr-defined]
            if isinstance(cur, dict) and cur.get("nonce") == nonce:
                backend.delete(_ADMIN_LOCK_PATH)  # type: ignore[attr-defined]
    except Exception:
        pass


def _wait_space_stage(api: object, target: str, *, timeout_s: int = 180) -> str:
    """Poll the Space runtime stage until it equals ``target`` or times out.
    Returns the last observed stage."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        stage = api.get_space_runtime(PROD_SPACE_ID).stage  # type: ignore[attr-defined]
        if stage != last:
            print(f"  space stage: {stage}", flush=True)
            last = stage
        if stage == target:
            return stage
        time.sleep(3)
    return last or "?"


def _space_base_url() -> str:
    """Public URL of the prod Space (HF convention: owner-name.hf.space)."""
    return f"https://{PROD_SPACE_ID.replace('/', '-').lower()}.hf.space"


def _http_status(url: str, timeout: float = 15.0) -> int:
    """GET *url*, return HTTP status (0 on connection error)."""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "inspector-admin-health"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001 — connection refused / timeout / DNS
        return 0


# Endpoints hit after every safe write. /healthz proves the app booted; the
# catalog endpoints read the deliveries table through the Pydantic models, so
# they 500 on a row that fails validation — the failure mode that a bare
# "stage == RUNNING" check misses entirely (an invalid write leaves the
# container RUNNING but every catalog read broken).
_HEALTH_RENDER_PATHS = ("/api/public/reciters?limit=1", "/api/public/stats")


def verify_post_write_health(*, healthz_timeout_s: int = 180) -> bool:
    """Confirm a post-restart Space is actually SERVING VALID DATA, not just up.

    Polls ``/healthz`` until the app answers 200 (container RUNNING != app
    ready, since boot re-pulls + migrates the DB), then renders the
    catalog/deliveries endpoints. Prints a PASS line or a loud FAIL banner;
    returns ``False`` on any failure so the caller can exit non-zero.
    """
    base = _space_base_url()
    deadline = time.time() + healthz_timeout_s
    hz = 0
    while time.time() < deadline:
        hz = _http_status(f"{base}/healthz")
        if hz == 200:
            break
        time.sleep(4)

    checks = [("/healthz", hz)]
    checks += [(p, _http_status(f"{base}{p}")) for p in _HEALTH_RENDER_PATHS]
    failed = [(p, c) for p, c in checks if c != 200]
    if not failed:
        print(f"  post-write health OK — {base} /healthz + catalog endpoints all 200", flush=True)
        return True
    print("\n" + "!" * 76, flush=True)
    print("POST-WRITE HEALTH CHECK FAILED — the write may have left prod broken.", flush=True)
    for p, c in failed:
        print(f"  FAIL  {p}  ->  HTTP {c or 'no-response'}", flush=True)
    print(f"  The Space is RUNNING but not serving valid data. Check {base}/?logs=container", flush=True)
    print("  A 500 on the catalog endpoints usually means the last write created a row that", flush=True)
    print("  fails model validation — revert it (e.g. admin_db.py exec ... --write).", flush=True)
    print("!" * 76 + "\n", flush=True)
    return False


@contextmanager
def prod_safe_setup(args: argparse.Namespace, **setup_kw) -> Iterator[BootstrapCtx]:
    """Single-writer guard for prod-mutating admin ops — yields a ``BootstrapCtx``.

    A live prod Space monotonically advances ``db_seq`` on every write, so it
    eventually overtakes and CLOBBERS any out-of-band bucket-DB edit (the sync
    CAS only refuses a *lower* seq). The ONLY safe path is single-writer: PAUSE
    the Space, pull fresh (its final state), mutate + sync, then RESUME — the
    Space boots and re-pulls the corrected DB (app.py boot does pull+migrate).

    Order matters: pause BEFORE the DB pull so the snapshot is the Space's final
    state. Resume runs in ``finally`` so a failed mutation never leaves prod
    paused. On dev this is a plain ``setup`` (no pause — single writer already).

    Two competing writers are both handled: the live Space (eliminated by the
    pause) AND another concurrent admin run (excluded by a TTL'd bucket advisory
    lock held across the whole pause→edit→resume window — a second admin aborts
    rather than overlapping and un-pausing the Space mid-edit).

    Usage::

        with prod_safe_setup(a, need_actor=False) as ctx:
            with durable_transaction() as con:
                con.execute(...)            # any bucket-DB mutation
    """
    global _SAFE_WINDOW
    prod = getattr(args, "prod", False)
    if not prod:
        # dev has no live Space — single writer already; just mark the window so
        # setup()'s guard treats the (harmless) dev mutation as sanctioned.
        _SAFE_WINDOW = True
        try:
            yield setup(args, **setup_kw)
        finally:
            _SAFE_WINDOW = False
        return

    _load_env()  # HF_TOKEN for HfApi + pause/restart
    # Bucket env + sys.path so get_backend() resolves the prod bucket for the lock.
    rr = repo_root()
    for p in (str(rr), str(rr / "inspector")):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ["INSPECTOR_BUCKET_REPO"] = BUCKETS["prod"]
    os.environ["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
    from services.storage.hf_bucket import get_backend

    backend = get_backend()
    nonce = uuid.uuid4().hex
    login = os.environ.get("INSPECTOR_DEV_OWNER_LOGIN", "admin").strip() or "admin"

    from huggingface_hub import HfApi, pause_space, restart_space

    api = HfApi()
    _acquire_admin_lock(backend, nonce, login)  # raises if another admin holds it
    try:
        print(f"pausing {PROD_SPACE_ID} (single-writer window) ...", flush=True)
        pause_space(PROD_SPACE_ID)
        stage = _wait_space_stage(api, "PAUSED", timeout_s=120)
        if stage != "PAUSED":
            print(f"space not confirmed PAUSED (stage={stage}); resuming, refusing to edit", flush=True)
            restart_space(PROD_SPACE_ID)
            raise RuntimeError(f"could not pause {PROD_SPACE_ID} for a safe write")
        _SAFE_WINDOW = True
        try:
            yield setup(args, **setup_kw)  # pull fresh under pause
        finally:
            print(f"resuming {PROD_SPACE_ID} ...", flush=True)
            restart_space(PROD_SPACE_ID)
            _wait_space_stage(api, "RUNNING", timeout_s=180)
            # Confirm the Space is serving valid data, not merely RUNNING. If the
            # write left a row that fails model validation the container is up but
            # every catalog read 500s — surface that loudly. Only raise when the
            # handler itself didn't already fail (don't mask the primary error).
            if not verify_post_write_health() and sys.exc_info()[0] is None:
                raise RuntimeError(
                    "post-write health check failed — prod is RUNNING but not "
                    "serving valid data (see banner above)"
                )
    finally:
        _SAFE_WINDOW = False
        _release_admin_lock(backend, nonce)


def run(args, handler, *, need_actor: bool = True, mutates: bool = True,
        safe_write: bool = False, need_db: bool = True):
    """Set up + run ``handler(ctx)``, prod-safe by construction.

    The single entrypoint for every admin command. Two independent signals:

    * ``mutates`` — the op has a prod side effect (DB write OR job launch); drives
      the ``--yes-prod`` gate + actor construction.
    * ``safe_write`` — the op does an IN-PROCESS bucket-DB write; when prod (and
      not ``--dry-run``) the WHOLE handler runs inside ``prod_safe_setup`` so the
      Space is paused single-writer for the write. Job launches set
      ``safe_write=False`` — they touch no DB in-process, so pausing is both
      unnecessary and harmful (it would break ``--monitor``'s webhook).

    ``handler`` takes the ``BootstrapCtx`` and returns the process exit code.
    """
    prod = getattr(args, "prod", False)
    dry = getattr(args, "dry_run", False)
    if safe_write and prod and not dry:
        with prod_safe_setup(args, need_actor=need_actor, need_db=need_db,
                             mutates=mutates, safe_write=True) as ctx:
            return handler(ctx)
    ctx = setup(args, need_actor=need_actor, need_db=need_db,
                mutates=mutates, safe_write=safe_write)
    return handler(ctx)


def after_write_banner(args: argparse.Namespace) -> None:
    if not getattr(args, "prod", False):
        return
    print()
    print("=" * 72)
    print(f"WROTE TO PROD BUCKET. If this op used prod_safe_setup(), the Space")
    print(f"({PROD_SPACE_ID}) was paused for the write and auto-resumed — the rows")
    print("are durable (the Space re-pulled the corrected DB on boot).")
    print("If it used bare setup(), the live Space will CLOBBER this write on its")
    print("next commit — re-do it through prod_safe_setup().")
    print("=" * 72)


def fmt_size(n: int | float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_iso(s: object) -> str:
    """ISO-string passthrough (handles None / datetimes)."""
    if s is None:
        return "—"
    if hasattr(s, "isoformat"):
        return s.isoformat(timespec="seconds")  # type: ignore[attr-defined]
    return str(s)
