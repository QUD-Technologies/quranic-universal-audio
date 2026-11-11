"""Integration smoke against the actual dev bucket.

Reads + writes under a ``_smoke/`` prefix on ``QUD-Technologies/quranic-inspector-bucket-dev``
to verify the BucketBackend wiring end-to-end. Requires ``HF_TOKEN`` in the
environment (loaded from repo-root ``.env`` by ``inspector/app.py`` at runtime;
this smoke loads it manually for direct module invocation).

Run with:

    python scripts/diagnostics/bucket_smoke.py

Cleans up after itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_repo_env() -> None:
    # Walk up from inspector/services/ to repo root and load .env.
    here = Path(__file__).resolve()
    for parent in here.parents:
        env = parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
        # Stop after a few levels — repo root has the .env we want.
        if (parent / ".git").exists() or parent.name == "":
            return


_load_repo_env()


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def smoke() -> int:
    bucket_id = os.environ.get("INSPECTOR_BUCKET_REPO", "QUD-Technologies/quranic-inspector-bucket-dev")

    from .hf_bucket import BucketBackend, StorageNotFound

    backend = BucketBackend(bucket_id=bucket_id)

    prefix = "_smoke_phase1"
    test_json = f"{prefix}/probe.json"
    test_jsonl = f"{prefix}/probe.jsonl"
    test_subdir_a = f"{prefix}/a/x.json"
    test_subdir_b = f"{prefix}/b/x.json"

    try:
        # Clean any leftovers from a previous failed run.
        for p in (test_json, test_jsonl, test_subdir_a, test_subdir_b):
            try:
                backend.delete(p)
            except Exception:
                pass

        # Round-trip JSON
        payload = {"smoke": True, "ts": "2026-05-12T00:00:00Z"}
        backend.write_json_atomic(test_json, payload)
        assert backend.exists(test_json), "exists() returned False after write"
        got = backend.read_json(test_json)
        assert got == payload, f"round-trip mismatch: {got!r} vs {payload!r}"
        print(f"ok  write_json_atomic + read_json round-trip on bucket {bucket_id}")

        # Missing read
        try:
            backend.read_json(f"{prefix}/does-not-exist.json")
        except StorageNotFound:
            print("ok  read_json raises StorageNotFound for missing path")
        else:
            raise AssertionError("expected StorageNotFound")

        # JSONL append + iterate
        for i in range(3):
            backend.append_jsonl(test_jsonl, {"i": i, "kind": "smoke"})
        records = list(backend.iter_jsonl(test_jsonl))
        assert len(records) == 3, f"expected 3 records, got {len(records)}"
        assert records[0]["i"] == 0 and records[2]["i"] == 2
        print("ok  append_jsonl + iter_jsonl round-trip")

        # list_dir
        backend.write_json_atomic(test_subdir_a, {"k": "a"})
        backend.write_json_atomic(test_subdir_b, {"k": "b"})
        children = backend.list_dir(prefix)
        # Expected: a, b, probe.json, probe.jsonl
        assert set(children) >= {"a", "b", "probe.json", "probe.jsonl"}, children
        print(f"ok  list_dir({prefix}) -> {children}")

        # list_dir on top-level should include v2 layout sibling dirs (state,
        # catalog, audit are absent until cutover seed; the dev bucket has
        # legacy segments/ + timestamps/ + manifest.json.gz).
        top = backend.list_dir("")
        assert prefix in top, f"expected {prefix} in top-level listing {top}"
        print(f"ok  list_dir top-level -> {top}")

        # copy
        backend.copy(test_subdir_a, f"{prefix}/a/y.json")
        copied = backend.read_json(f"{prefix}/a/y.json")
        assert copied == {"k": "a"}
        print("ok  copy preserves content")

        # delete
        backend.delete(f"{prefix}/a/y.json")
        assert not backend.exists(f"{prefix}/a/y.json")
        print("ok  delete removes file")

        # Cleanup
        for p in (test_json, test_jsonl, test_subdir_a, test_subdir_b):
            backend.delete(p)
        print("ok  cleanup complete")

        print("bucket smoke: all checks passed")
        return 0
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        # Best-effort cleanup even on failure.
        for p in (test_json, test_jsonl, test_subdir_a, test_subdir_b, f"{prefix}/a/y.json"):
            try:
                backend.delete(p)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(smoke())
