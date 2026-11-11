#!/usr/bin/env python3
"""One launcher for every way to run the Inspector - conflict-free, any worktree.

The repo has many moving parts (Flask backend, Vite dev server, a dev/prod HF
bucket, per-worktree SQLite, a half-dozen ``INSPECTOR_*`` knobs) and several
worktrees that all want to run at once. Driving that by hand is where the
"not connecting / wrong port / two Flasks on :5000 / stale Vite serving the
wrong branch" failures come from. This script owns all of it:

  * picks FREE ports (never collides with the OS or another launch),
  * isolates each worktree's SQLite (``INSPECTOR_DB_PATH``) so parallel stacks
    never clobber each other's DB,
  * uses ``sys.executable`` for the backend so the interpreter can't drift,
  * wires Vite's proxy to the matching backend (or a remote Space),
  * waits for real readiness (``/healthz`` + the Vite port) before returning,
  * records every running stack in one machine-wide registry so ``list`` /
    ``down`` / ``doctor`` can see and clean them - including the foreign
    double-bind that silently serves stale code.

Every mode runs FULLY LOCAL — your branch's Flask backend + Vite, no HF Space
proxy, no hf-mount. Bucket reads go through the hffs fallback (sub-second on
Windows); audio CDN-falls-back via the proxy. The only thing that changes
between modes is which data the backend reads.

Modes (``--mode``, default ``dev``):
  dev    (default) local Flask + Vite reading the DEV bucket (read-write). The
         everyday mode: runs your branch end-to-end, audio + analysis included.
  prod   local Flask + Vite reading the PROD bucket, READ-ONLY. Two guards:
         INSPECTOR_READ_ONLY=1 makes the storage backend refuse every write
         (segment saves, manifests, job records), and INSPECTOR_DB_SYNC=0 keeps
         DB commits local — nothing local can mutate production by any path.
  fixtures  fully offline (filesystem backend, seeded fixtures) + Vite.

Commands:
  up      (default) start a stack; prints human URLs + a machine block
  list    show every registered stack with live health
  down    stop a stack (--worktree / --id / --port / --all)
  doctor  find & optionally --fix port conflicts, double-binds, stale procs

Examples:
  python scripts/devenv/launch.py                     # dev, this worktree
  python scripts/devenv/launch.py up --mode prod         # read-only prod data
  python scripts/devenv/launch.py up --no-vite           # local backend only
  python scripts/devenv/launch.py list
  python scripts/devenv/launch.py down --all
  python scripts/devenv/launch.py doctor --fix
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import psutil  # fast, reliable cross-platform process/port introspection
except Exception:  # noqa: BLE001 - optional; stdlib fallbacks below
    psutil = None

IS_WIN = os.name == "nt"
# Suppress the console window every native-exe subprocess (git/netstat/taskkill/
# node/python) would otherwise flash on Windows. No-op off Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

# Preferred port windows - the allocator walks these, skipping anything in use
# (OS-listening or reserved by another registered stack).
VITE_PORT_RANGE = range(5173, 5274)
BACKEND_PORT_RANGE = range(5000, 5100)

# Prod bucket repo id — `prod` mode points the local backend here, read-only.
PROD_BUCKET_REPO = "QUD-Technologies/quranic-inspector-bucket"

MODES = ("dev", "prod", "fixtures")


# ---------------------------------------------------------------------------
# Git worktree resolution
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path | None = None) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
        creationflags=NO_WINDOW,
    )
    return out.stdout.strip()


def worktree_root(start: Path | None = None) -> Path:
    """The worktree containing ``start`` (default cwd)."""
    return Path(_git(["rev-parse", "--show-toplevel"], cwd=start)).resolve()


def main_root() -> Path:
    """The primary worktree - parent of the shared git common dir. The registry
    + logs live here so every worktree's launches share one machine-wide view."""
    common = Path(_git(["rev-parse", "--git-common-dir"])).resolve()
    return common.parent


def resolve_worktree(name_or_path: str | None) -> Path:
    """Resolve a ``--worktree`` argument (name, path, or None=cwd) to a root."""
    if not name_or_path:
        return worktree_root()
    p = Path(name_or_path)
    if p.is_dir():
        return worktree_root(p)
    # Treat as a worktree leaf name: match against `git worktree list`.
    for line in _git(["worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            wt = Path(line[len("worktree ") :]).resolve()
            if wt.name == name_or_path:
                return wt
    raise SystemExit(f"launch: no worktree named or at {name_or_path!r}")


def frontend_dir(root: Path) -> Path:
    return root / "inspector" / "frontend"


# ---------------------------------------------------------------------------
# Ports + processes (psutil-preferred, stdlib fallback)
# ---------------------------------------------------------------------------


def port_is_free(port: int) -> bool:
    """True if nothing is listening - confirmed by an actual bind on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def port_listeners(port: int) -> list[int]:
    """PIDs LISTENING on ``port`` (may be >1 - that's the double-bind bug)."""
    pids: list[int] = []
    if psutil is not None:
        for c in psutil.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                pids.append(c.pid)
        return sorted(set(pids))
    # Fallback: parse netstat (Windows) / ss (POSIX).
    try:
        if IS_WIN:
            out = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, creationflags=NO_WINDOW
            ).stdout
            for ln in out.splitlines():
                parts = ln.split()
                if len(parts) >= 5 and parts[0].startswith("TCP") and parts[3] == "LISTENING":
                    if parts[1].rsplit(":", 1)[-1] == str(port):
                        with contextlib.suppress(ValueError):
                            pids.append(int(parts[4]))
        else:
            out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
            for ln in out.splitlines():
                if f":{port} " in ln and "pid=" in ln:
                    with contextlib.suppress(Exception):
                        pids.append(int(ln.split("pid=")[1].split(",")[0]))
    except Exception:  # noqa: BLE001
        pass
    return sorted(set(pids))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        return psutil.pid_exists(pid)
    if IS_WIN:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            creationflags=NO_WINDOW,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_tree(pid: int) -> None:
    """Terminate a process and its children (npm spawns node; flask is lone)."""
    if pid <= 0 or not pid_alive(pid):
        return
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            procs = proc.children(recursive=True) + [proc]
            for p in procs:
                with contextlib.suppress(Exception):
                    p.terminate()
            _, alive = psutil.wait_procs(procs, timeout=4)
            for p in alive:
                with contextlib.suppress(Exception):
                    p.kill()
            return
        except Exception:  # noqa: BLE001
            pass
    if IS_WIN:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)


def spawn_detached(cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> int:
    """Launch ``cmd`` fully detached, stdout+stderr -> ``log_path``. Returns pid."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab", buffering=0)  # noqa: SIM115 - handed to the child
    kwargs: dict = dict(
        cwd=str(cwd), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
    )
    if IS_WIN:
        # CREATE_NO_WINDOW (not DETACHED_PROCESS): the child gets a HIDDEN
        # console that its own children (node→esbuild, etc.) inherit, so they
        # never pop a new console window. DETACHED_PROCESS gives no console at
        # all, which makes every console-app grandchild flash its own window.
        # NEW_PROCESS_GROUP keeps it detached enough to survive our exit.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603
    return proc.pid


def vite_cmd(root: Path) -> list[str]:
    """Run Vite via node + its JS entry directly, NOT `npm run dev`. On Windows a
    detached (console-less) process can't execute the `npm.cmd` batch wrapper, so
    it dies silently with an empty log; node.exe runs clean detached on every OS."""
    from shutil import which

    node = which("node")
    if not node:
        raise SystemExit("launch: node not found on PATH (run scripts/devenv/setup.sh)")
    vite_js = frontend_dir(root) / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_js.is_file():
        raise SystemExit(
            f"launch: Vite not installed at {vite_js} (run `npm ci` in inspector/frontend)"
        )
    return [node, str(vite_js)]


# ---------------------------------------------------------------------------
# Registry (one machine-wide JSON under the MAIN worktree's .local/launch)
# ---------------------------------------------------------------------------


@dataclass
class Stack:
    id: str
    worktree: str
    mode: str
    vite_port: int | None
    backend_port: int | None
    backend_pid: int = 0
    vite_pid: int = 0
    url: str = ""
    started_at: float = 0.0
    extra: dict = field(default_factory=dict)


def _registry_dir() -> Path:
    d = main_root() / ".local" / "launch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return _registry_dir() / "registry.json"


@contextlib.contextmanager
def _registry_lock():
    """Crude cross-process lock so parallel `up`s don't grab the same port.

    Atomic O_EXCL create; spin briefly, then steal a stale (>30s) lock."""
    lock = _registry_dir() / "registry.lock"
    deadline = time.time() + 10
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
            if time.time() > deadline:
                break  # proceed unlocked rather than wedge
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def load_stacks() -> list[Stack]:
    p = _registry_path()
    if not p.exists():
        return []
    try:
        return [Stack(**s) for s in json.loads(p.read_text("utf-8"))]
    except Exception:  # noqa: BLE001
        return []


def save_stacks(stacks: list[Stack]) -> None:
    _registry_path().write_text(json.dumps([asdict(s) for s in stacks], indent=2), "utf-8")


def prune_dead(stacks: list[Stack]) -> list[Stack]:
    """Drop stacks whose processes are gone (keeps the registry honest)."""
    alive = []
    for s in stacks:
        be_ok = s.backend_pid and pid_alive(s.backend_pid)
        fe_ok = s.vite_pid and pid_alive(s.vite_pid)
        if be_ok or fe_ok:
            alive.append(s)
    return alive


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


def alloc_port(rng: range, reserved: set[int]) -> int:
    for port in rng:
        if port in reserved:
            continue
        if port_is_free(port) and not port_listeners(port):
            return port
    raise SystemExit(f"launch: no free port in {rng.start}-{rng.stop}")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _http(url: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except Exception:  # noqa: BLE001
        return 0, b""


def wait_backend(port: int, timeout: float, log: Path) -> dict:
    """Poll /healthz until the substrate is loaded with reciters. Local mode
    returns 200 even while 'degraded' (no mount), so gate on state + count."""
    url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        if not _backend_log_ok(log):
            raise SystemExit(f"launch: backend exited during boot - see {log}")
        code, body = _http(url)
        if code == 200:
            with contextlib.suppress(Exception):
                last = json.loads(body)
                if last.get("state_loaded") and last.get("reciters_count", 0) > 0:
                    return last
        time.sleep(1.0)
    raise SystemExit(
        f"launch: backend not ready in {int(timeout)}s (last={last or 'no response'}); see {log}"
    )


def _backend_log_ok(log: Path) -> bool:
    """Cheap crash check: a traceback in the log means boot died."""
    if not log.exists():
        return True
    with contextlib.suppress(Exception):
        tail = log.read_bytes()[-2000:].decode("utf-8", "replace")
        if "Traceback (most recent call last)" in tail and "Starting server at" not in tail:
            return False
    return True


def wait_vite(port: int, timeout: float, check_api: bool) -> None:
    # Vite binds `localhost` (IPv6 ::1 on Windows), NOT 127.0.0.1 — probe via the
    # hostname so the family matches; the backend (0.0.0.0) is fine on 127.
    base = f"http://localhost:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, _ = _http(base + "/")
        if code == 200:
            if not check_api:
                return
            api, _ = _http(base + "/api/public/version", timeout=8.0)
            if api == 200:
                return
        time.sleep(0.5)
    raise SystemExit(f"launch: Vite not ready on {port} in {int(timeout)}s")


# ---------------------------------------------------------------------------
# Env builders per mode
# ---------------------------------------------------------------------------


def ensure_fixtures(root: Path) -> Path:
    fx = root / "inspector" / ".fixtures"
    if (fx / "db" / "inspector.db").is_file():
        return fx
    print("launch: seeding offline fixtures (one-time download)...", flush=True)
    subprocess.run(
        [sys.executable, "scripts/devenv/seed_fixtures.py"],
        cwd=str(root),
        check=True,
        creationflags=NO_WINDOW,
    )
    if not (fx / "db" / "inspector.db").is_file():
        raise SystemExit(
            "launch: fixtures seed did not produce inspector/.fixtures/db/inspector.db"
        )
    return fx


# The full set of run-mode knobs the launcher OWNS. app.py loads `.env` only for
# keys not already set, so by setting every one of these we guarantee `.env` can
# only ever supply secrets + identity (HF/GH/QF tokens, session secret, dev-owner
# id) and — for dev — the contributor's own dev-bucket id. No mode knob leaks in.
_OWNED_KNOBS = (
    "INSPECTOR_BACKEND",
    "INSPECTOR_FILESYSTEM_ROOT",
    "INSPECTOR_ALLOW_PROD_BUCKET",
    "INSPECTOR_READ_ONLY",
    "INSPECTOR_DB_SYNC",
    "INSPECTOR_AUTO_MOUNT",
    "INSPECTOR_AUDIO_FROM_BUCKET",
    "INSPECTOR_PEAKS_FROM_BUCKET",
)


def backend_env(
    root: Path, mode: str, *, bucket_audio: bool = False, ffmpeg_peaks: bool = False
) -> dict[str, str]:
    """Per-worktree backend env, authoritative for every run-mode knob.

    The launcher determines the run profile — which backend, which bucket,
    read-only, sync, mount — so it sets all of `_OWNED_KNOBS` explicitly and
    `.env` can only fill the rest (secrets/identity). The DB path is the
    load-bearing isolation knob: the default is a SHARED tempdir file, so two
    worktrees would clobber one DB (and WinError-5 on os.replace). Each worktree
    gets its own.
    """
    env = os.environ.copy()
    # Clear any inherited mode knobs so a stale shell/.env value can't perturb
    # the profile; each branch below re-sets exactly what it needs.
    for k in _OWNED_KNOBS:
        env.pop(k, None)
    env["INSPECTOR_DB_PATH"] = str(root / ".local" / "launch" / "inspector.db")
    env.setdefault("INSPECTOR_RELEASE_POLL", "0")  # dev: no HF-Job polling loop

    # Peaks honour --ffmpeg-peaks in every mode (ffmpeg works offline too).
    env["INSPECTOR_PEAKS_FROM_BUCKET"] = "0" if ffmpeg_peaks else "1"

    if mode == "fixtures":
        fx = ensure_fixtures(root)
        env["INSPECTOR_BACKEND"] = "filesystem"
        env["INSPECTOR_FILESYSTEM_ROOT"] = str(fx)
        env["INSPECTOR_AUTO_MOUNT"] = "0"
        # Offline: audio must come from the local fixtures, never the CDN.
        env["INSPECTOR_AUDIO_FROM_BUCKET"] = "1"
        return env

    # dev / prod read a bucket.
    env["INSPECTOR_BACKEND"] = "bucket"
    # Audio: default to the CDN (fast); the bucket's hffs full-MP3 read is slow
    # locally. --bucket-audio forces the bucket (to verify re-encoded audio).
    env["INSPECTOR_AUDIO_FROM_BUCKET"] = "1" if bucket_audio else "0"
    if mode == "prod":
        # PROD bucket, READ-ONLY. Two guards so nothing local can mutate prod:
        #   INSPECTOR_READ_ONLY=1 — the storage backend refuses EVERY write
        #     (segment save, manifests, job records — not just the DB), and
        #   INSPECTOR_DB_SYNC=0   — DB commits stay local, never upload.
        # ALLOW_PROD_BUCKET=1 acknowledges the prod-bucket guard so it resolves.
        env["INSPECTOR_BUCKET_REPO"] = PROD_BUCKET_REPO
        env["INSPECTOR_ALLOW_PROD_BUCKET"] = "1"
        env["INSPECTOR_READ_ONLY"] = "1"
        env["INSPECTOR_DB_SYNC"] = "0"
    else:  # dev: DEV bucket, read-write. Forbid prod outright — even if `.env`
        # opts into prod, dev mode can never reach it (resolve_bucket_repo
        # refuses when ALLOW != "1"). INSPECTOR_BUCKET_REPO is the ONE knob left
        # to `.env`: a contributor's own dev bucket, else the shared dev default.
        env["INSPECTOR_ALLOW_PROD_BUCKET"] = "0"
    return env


def vite_env(vite_port: int, backend_port: int) -> dict[str, str]:
    env = os.environ.copy()
    env["INSPECTOR_VITE_PORT"] = str(vite_port)
    env["INSPECTOR_BACKEND_PORT"] = str(backend_port)
    env["INSPECTOR_BACKEND_HOST"] = "127.0.0.1"
    # Never let a stray INSPECTOR_API_TARGET (render harness) redirect /api to a
    # remote Space — every launch mode talks to its own local backend.
    env.pop("INSPECTOR_API_TARGET", None)
    return env


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    root = resolve_worktree(args.worktree)
    mode = args.mode
    want_backend = not args.no_backend
    want_vite = not args.no_vite
    logs = root / ".local" / "launch" / "logs"
    short = root.name

    if want_vite and not want_backend:
        raise SystemExit(
            "launch: --no-backend needs --no-vite too (Vite proxies /api to the "
            "local backend; there's no remote target any more)."
        )

    if mode == "prod":
        print(
            "launch: NOTE - reading the PROD bucket READ-ONLY (write-back disarmed). "
            "Edits won't persist; use --mode dev to actually change data.",
            flush=True,
        )

    if (
        mode in ("dev", "prod")
        and want_backend
        and not (os.environ.get("HF_TOKEN") or _env_file_has(root, "HF_TOKEN"))
    ):
        print(
            "launch: WARNING - no HF_TOKEN in env or .env; bucket reads will fail. "
            "Use --mode fixtures for offline, or add HF_TOKEN.",
            flush=True,
        )

    with _registry_lock():
        stacks = prune_dead(load_stacks())
        # Single-writer invariant: dev mode is read-WRITE on the shared dev
        # bucket (full-file DB sync + last-write-wins content). A second dev
        # writer — here or the live dev Space — can clobber the other's writes.
        if mode == "dev" and want_backend:
            others = [s for s in stacks if s.mode == "dev" and Path(s.worktree) != root]
            if others:
                names = ", ".join(s.id for s in others)
                print(
                    f"launch: WARNING - {len(others)} other dev stack(s) already writing the "
                    f"shared dev bucket ({names}). Concurrent dev writers can clobber each "
                    "other's DB/edits (single-writer invariant). Use --mode prod/fixtures for "
                    "a safe parallel stack, or edit in only one at a time.",
                    flush=True,
                )
        existing = [s for s in stacks if Path(s.worktree) == root]
        if existing and not args.force:
            s = existing[0]
            print(
                f"launch: a stack for '{short}' is already up ({_url(s)}). "
                f"Use --force for a second, or `down --worktree {short}` first.",
                flush=True,
            )
            _print_summary(s)
            return 0
        reserved = {s.vite_port for s in stacks if s.vite_port} | {
            s.backend_port for s in stacks if s.backend_port
        }
        vite_port = alloc_port(VITE_PORT_RANGE, reserved) if want_vite else None
        if vite_port:
            reserved.add(vite_port)
        backend_port = alloc_port(BACKEND_PORT_RANGE, reserved) if want_backend else None
        sid = f"{short}-{int(time.time())}"
        stack = Stack(
            id=sid,
            worktree=str(root),
            mode=mode,
            vite_port=vite_port,
            backend_port=backend_port,
            started_at=time.time(),
        )
        stacks.append(stack)  # reserve ports immediately so a parallel `up` skips them
        save_stacks(stacks)

    try:
        if want_backend:
            assert backend_port is not None  # want_backend => allocated above
            print(
                f"launch: starting backend on :{backend_port} (mode={mode}, db isolated to {short})...",
                flush=True,
            )
            pid = spawn_detached(
                [sys.executable, "inspector/app.py", "--port", str(backend_port)],
                cwd=root,
                env=backend_env(
                    root, mode, bucket_audio=args.bucket_audio, ffmpeg_peaks=args.ffmpeg_peaks
                ),
                log_path=logs / f"backend-{backend_port}.log",
            )
            stack.backend_pid = pid
            _update_stack(stack)
            health = wait_backend(
                backend_port, timeout=args.timeout, log=logs / f"backend-{backend_port}.log"
            )
            stack.extra["reciters"] = health.get("reciters_count")
            print(
                f"launch: backend ready - {health.get('reciters_count')} reciters, db schema v{health.get('db', {}).get('schema_version')}",
                flush=True,
            )

        if want_vite:
            assert vite_port is not None  # want_vite => allocated above
            assert backend_port is not None  # want_vite always pairs with a backend
            print(
                f"launch: starting Vite on :{vite_port} (proxy -> local :{backend_port})...",
                flush=True,
            )
            pid = spawn_detached(
                vite_cmd(root),
                cwd=frontend_dir(root),
                env=vite_env(vite_port, backend_port),
                log_path=logs / f"vite-{vite_port}.log",
            )
            stack.vite_pid = pid
            stack.url = f"http://localhost:{vite_port}"
            _update_stack(stack)
            # Cold first run pre-bundles deps (slow on Windows) — give it room.
            wait_vite(vite_port, timeout=150, check_api=want_backend)
            print("launch: Vite ready", flush=True)
        elif want_backend:
            stack.url = f"http://localhost:{backend_port}"
            _update_stack(stack)
    except SystemExit:
        print("launch: startup failed - rolling back this stack", flush=True)
        _stop_stack(stack)
        raise

    _print_summary(stack)
    if args.open and stack.url:
        _open_browser(stack.url)
    return 0


def _url(s: Stack) -> str:
    return s.url or (
        f"http://localhost:{s.vite_port}" if s.vite_port else f"http://localhost:{s.backend_port}"
    )


def _update_stack(stack: Stack) -> None:
    with _registry_lock():
        stacks = load_stacks()
        stacks = [stack if s.id == stack.id else s for s in stacks]
        if all(s.id != stack.id for s in stacks):
            stacks.append(stack)
        save_stacks(stacks)


def _print_summary(stack: Stack) -> None:
    print("", flush=True)
    print(f"  > {stack.mode} stack ready - open {_url(stack)}", flush=True)
    if stack.vite_port:
        print(
            f"     Vite     http://localhost:{stack.vite_port}  (pid {stack.vite_pid})", flush=True
        )
    if stack.backend_port:
        print(
            f"     Backend  http://localhost:{stack.backend_port}/healthz  (pid {stack.backend_pid})",
            flush=True,
        )
    print(f"     Worktree {Path(stack.worktree).name}   id {stack.id}", flush=True)
    print(f"     Stop     python scripts/devenv/launch.py down --id {stack.id}", flush=True)
    # Machine-readable block for an agent driving this.
    print(
        "LAUNCH_JSON "
        + json.dumps(
            {
                "id": stack.id,
                "url": _url(stack),
                "mode": stack.mode,
                "vite_port": stack.vite_port,
                "backend_port": stack.backend_port,
                "worktree": stack.worktree,
            }
        ),
        flush=True,
    )


def _env_file_has(root: Path, key: str) -> bool:
    f = root / ".env"
    if not f.exists():
        return False
    with contextlib.suppress(Exception):
        return any(ln.strip().startswith(key + "=") for ln in f.read_text("utf-8").splitlines())
    return False


def _open_browser(url: str) -> None:
    import webbrowser

    with contextlib.suppress(Exception):
        webbrowser.open(url)


# ---------------------------------------------------------------------------
# list / down / doctor
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    with _registry_lock():
        stacks = prune_dead(load_stacks())
        save_stacks(stacks)
    if not stacks:
        print("launch: no stacks running.", flush=True)
        return 0
    for s in stacks:
        health = "?"
        if s.backend_port:
            code, body = _http(f"http://127.0.0.1:{s.backend_port}/healthz")
            if code == 200:
                with contextlib.suppress(Exception):
                    health = f"{json.loads(body).get('reciters_count')} reciters"
        elif s.vite_port:
            code, _ = _http(f"http://localhost:{s.vite_port}/")
            health = "up" if code == 200 else "down"
        print(
            f"  {s.id:28}  {s.mode:11}  {_url(s):26}  be:{s.backend_port or '-'} fe:{s.vite_port or '-'}  {health}",
            flush=True,
        )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    with _registry_lock():
        stacks = prune_dead(load_stacks())
        if args.all:
            targets = list(stacks)
        elif args.id:
            targets = [s for s in stacks if s.id == args.id]
        elif args.port:
            targets = [s for s in stacks if args.port in (s.vite_port, s.backend_port)]
        elif args.worktree:
            root = resolve_worktree(args.worktree)
            targets = [s for s in stacks if Path(s.worktree) == root]
        else:
            root = worktree_root()
            targets = [s for s in stacks if Path(s.worktree) == root]
        if not targets:
            print("launch: no matching stack to stop.", flush=True)
            return 0
        for s in targets:
            _stop_stack(s)
            print(f"launch: stopped {s.id}", flush=True)
        remaining = [s for s in stacks if s not in targets]
        save_stacks(remaining)
    return 0


def _stop_stack(s: Stack) -> None:
    if s.vite_pid:
        kill_tree(s.vite_pid)
    if s.backend_pid:
        kill_tree(s.backend_pid)


def cmd_doctor(args: argparse.Namespace) -> int:
    stacks = load_stacks()
    issues: list[str] = []
    conflict_pids: list[int] = []  # real conflicts — safe to kill on --fix
    orphan_pids: list[int] = []  # untracked Inspector procs — only with --kill-orphans

    # 1. Registry entries whose processes are dead → prune on --fix.
    dead = [s for s in stacks if not (pid_alive(s.backend_pid) or pid_alive(s.vite_pid))]
    for s in dead:
        issues.append(f"stale registry entry {s.id} (no live process)")

    # 2. Double-bind / foreign listener on a REGISTERED port (the :5000 bug —
    #    a second process serving stale code on a port we own). Real conflict.
    known_pids = {s.backend_pid for s in stacks} | {s.vite_pid for s in stacks}
    for s in stacks:
        for label, port, owner in (
            ("backend", s.backend_port, s.backend_pid),
            ("vite", s.vite_port, s.vite_pid),
        ):
            if not port:
                continue
            listeners = port_listeners(port)
            extras = [p for p in listeners if p != owner]
            if len(listeners) > 1:
                issues.append(
                    f"{label} port {port} has {len(listeners)} listeners {listeners} (double-bind -> serves stale code)"
                )
            for p in extras:
                if p not in known_pids:
                    issues.append(
                        f"{label} port {port} owned by foreign pid {p} (not the registered {owner})"
                    )
                conflict_pids.append(p)

    # 3. Orphan Inspector processes not started by launch (other worktrees, manual
    #    `npm run dev`, failed runs). Reported, but NOT killed by plain --fix —
    #    they may be a deliberate run. Use --kill-orphans to clear them.
    for pid, what in _scan_orphans():
        if pid not in known_pids and pid not in conflict_pids:
            issues.append(f"orphan {what} pid {pid} (not tracked by launch)")
            orphan_pids.append(pid)

    if not issues:
        print("launch doctor: clean - no port conflicts, double-binds, or orphans.", flush=True)
        return 0
    print("launch doctor found:", flush=True)
    for i in issues:
        print(f"  x {i}", flush=True)

    if not args.fix:
        hint = "run `launch doctor --fix` to prune dead entries + kill conflicting processes"
        if orphan_pids:
            hint += f"; add --kill-orphans to also stop {len(orphan_pids)} untracked Inspector process(es)"
        print(f"  {hint}.", flush=True)
        return 1

    to_kill = set(conflict_pids)
    if args.kill_orphans:
        to_kill |= set(orphan_pids)
    for pid in sorted(to_kill):
        kill_tree(pid)
        print(f"  -> killed pid {pid}", flush=True)
    with _registry_lock():
        save_stacks(prune_dead(load_stacks()))
    if orphan_pids and not args.kill_orphans:
        print(
            f"  ({len(orphan_pids)} untracked process(es) left alone — re-run with --kill-orphans to stop them)",
            flush=True,
        )
    print("launch doctor: fixes applied.", flush=True)
    return 0


def _scan_orphans() -> list[tuple[int, str]]:
    """Inspector backends / Vite servers running on this machine, by cmdline."""
    found: list[tuple[int, str]] = []
    if psutil is None:
        return found
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(p.info.get("cmdline") or [])
        if "inspector/app.py" in cmd or "inspector\\app.py" in cmd:
            found.append((p.info["pid"], "flask backend"))
        elif "vite" in cmd and "inspector" in cmd.lower():
            found.append((p.info["pid"], "vite"))
    return found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="launch", description="Run the Inspector - any mode, any worktree, conflict-free."
    )
    sub = p.add_subparsers(dest="cmd")

    up = sub.add_parser("up", help="start a stack (default command)")
    up.add_argument("--mode", choices=MODES, default="dev")
    up.add_argument("--worktree", help="worktree name or path (default: current)")
    up.add_argument("--no-vite", action="store_true", help="backend only")
    up.add_argument("--no-backend", action="store_true", help="(requires --no-vite)")
    up.add_argument(
        "--bucket-audio",
        action="store_true",
        help="read audio from the bucket instead of the CDN (slower; verifies re-encoded audio)",
    )
    up.add_argument(
        "--ffmpeg-peaks",
        action="store_true",
        help="skip bucket peaks; compute per-segment peaks via ffmpeg",
    )
    up.add_argument("--open", action="store_true", help="open the URL in a browser")
    up.add_argument(
        "--force", action="store_true", help="start a second stack for a worktree already up"
    )
    up.add_argument("--timeout", type=float, default=180.0, help="backend readiness timeout (s)")
    up.set_defaults(func=cmd_up)

    ls = sub.add_parser("list", help="show running stacks")
    ls.set_defaults(func=cmd_list)

    dn = sub.add_parser("down", help="stop a stack")
    g = dn.add_mutually_exclusive_group()
    g.add_argument("--worktree", help="stop the stack for this worktree (default: current)")
    g.add_argument("--id", help="stop by stack id")
    g.add_argument("--port", type=int, help="stop the stack using this port")
    g.add_argument("--all", action="store_true", help="stop every stack")
    dn.set_defaults(func=cmd_down)

    dr = sub.add_parser("doctor", help="find/fix port conflicts, double-binds, orphans")
    dr.add_argument(
        "--fix",
        action="store_true",
        help="prune dead entries + kill conflicting (double-bind/foreign) processes",
    )
    dr.add_argument(
        "--kill-orphans",
        action="store_true",
        help="with --fix, also stop untracked Inspector processes (other worktrees / manual runs)",
    )
    dr.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    # Bare `launch` and `launch --mode X` default to `up`.
    if not argv or (argv[0] not in {"up", "list", "down", "doctor", "-h", "--help"}):
        argv = ["up", *argv]
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
