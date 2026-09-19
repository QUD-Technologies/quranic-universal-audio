"""Tests for the shipped consumer helper ``qua_jobs/check_updates.py``.

The helper diffs a downloaded dataset-level ``manifest.json`` (baseline) against
the latest release's manifest, per reciter, keyed on ``content_hash``. These
tests exercise the pure ``compute_updates`` core and the CLI exit code via the
offline ``--latest-file`` path (no network).
"""

from __future__ import annotations

import json

from qua_jobs.check_updates import (
    STATUS_NEW,
    STATUS_UPDATED,
    compute_updates,
    has_changes,
    repo_from_manifest,
)


def _manifest(version: str, recs: dict[str, str]) -> dict:
    """Build a manifest dict from ``{slug: content_hash}``."""
    return {
        "release_version": version,
        "recitations": {
            slug: {
                "content_hash": h,
                "ts_version": f"{version}-ts",
                "zip_url": f"https://github.com/QUD-Technologies/quranic-universal-audio/releases/download/{version}/{slug}.zip",
            }
            for slug, h in recs.items()
        },
    }


BASELINE = _manifest("v0.2.0", {"reciter_a": "aaa", "reciter_b": "bbb"})
LATEST = _manifest("v0.3.0", {"reciter_a": "AAA", "reciter_b": "bbb", "reciter_c": "ccc"})


def test_default_scope_flags_only_changed_baseline_reciters():
    result = compute_updates(BASELINE, LATEST)
    assert result["checked"] == 2  # only the two in the baseline
    assert [u["slug"] for u in result["updates"]] == ["reciter_a"]
    assert result["updates"][0]["status"] == STATUS_UPDATED
    assert result["updates"][0]["ts_version"] == "v0.3.0-ts"
    assert result["up_to_date"] == ["reciter_b"]
    assert result["removed"] == []
    assert has_changes(result) is True


def test_reciters_scope_surfaces_newly_available():
    result = compute_updates(BASELINE, LATEST, reciters=["reciter_a", "reciter_b", "reciter_c"])
    by_slug = {u["slug"]: u["status"] for u in result["updates"]}
    assert by_slug == {"reciter_a": STATUS_UPDATED, "reciter_c": STATUS_NEW}
    assert result["up_to_date"] == ["reciter_b"]


def test_unknown_slug_reported_not_an_update():
    result = compute_updates(BASELINE, LATEST, reciters=["nope"])
    assert result["unknown"] == ["nope"]
    assert result["updates"] == []
    assert has_changes(result) is False


def test_removed_reciter_is_a_change():
    latest_without_b = _manifest("v0.3.0", {"reciter_a": "aaa"})
    result = compute_updates(BASELINE, latest_without_b)
    assert result["removed"] == ["reciter_b"]
    assert has_changes(result) is True


def test_all_current_no_changes():
    result = compute_updates(BASELINE, BASELINE)
    assert result["updates"] == []
    assert result["up_to_date"] == ["reciter_a", "reciter_b"]
    assert has_changes(result) is False


def test_repo_derived_from_manifest_zip_url():
    assert repo_from_manifest(BASELINE) == "QUD-Technologies/quranic-universal-audio"
    assert repo_from_manifest({"recitations": {}}) is None


def test_cli_exit_code_and_json(tmp_path, capsys):
    from qua_jobs.check_updates import main

    base = tmp_path / "manifest.json"
    base.write_text(json.dumps(BASELINE), encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps(LATEST), encoding="utf-8")

    rc = main([str(base), "--latest-file", str(latest), "--json"])
    assert rc == 1  # reciter_a changed → non-zero so a CI run "fails" and notifies
    payload = json.loads(capsys.readouterr().out)
    assert [u["slug"] for u in payload["updates"]] == ["reciter_a"]


def test_cli_exit_zero_when_current(tmp_path, capsys):
    from qua_jobs.check_updates import main

    base = tmp_path / "manifest.json"
    base.write_text(json.dumps(BASELINE), encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps(BASELINE), encoding="utf-8")

    rc = main([str(base), "--latest-file", str(latest)])
    assert rc == 0
    assert "up to date" in capsys.readouterr().out
