"""GET /api/seg/validate/<reciter> response-shape tests (MUST-1)."""

from __future__ import annotations

from tests.conftest import assert_keys_superset


def test_validate_response_shape(flask_client, tmp_reciter_dir, load_expected):
    """The validate route returns at least the frozen MUST-1 baseline field set."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")

    baseline = load_expected("112-ikhlas", "routes")
    expected_keys = baseline["validate"]["field_keys_top_level"]

    res = flask_client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert isinstance(body, dict)
    assert_keys_superset(expected_keys, list(body.keys()), "GET /api/seg/validate")


def test_validate_includes_classified_issues_field_per_snapshot(flask_client, tmp_reciter_dir):
    """Every issue item should carry a classified_issues: string[] field after Phase 2.

    Walks the per-category arrays at the top of the response (``failed``,
    ``low_confidence``, ``cross_verse``, ``audio_bleeding``, ``repetitions``,
    ``muqattaat``, ``qalqala``) plus any nested ``by_*`` container the response may grow in later phases.
    """
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "synthetic-classifier")

    res = flask_client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200
    body = res.get_json()

    per_segment_array_keys = {
        "failed",
        "low_confidence",
        "cross_verse",
        "audio_bleeding",
        "repetitions",
        "muqattaat",
        "qalqala",
    }

    issues_lists: list[list] = []
    for top_key in per_segment_array_keys:
        v = body.get(top_key)
        if isinstance(v, list):
            issues_lists.append(v)
    for top_key in ("issues", "details", "by_chapter"):
        v = body.get(top_key)
        if isinstance(v, list):
            issues_lists.append(v)
        elif isinstance(v, dict):
            for inner in v.values():
                if isinstance(inner, list):
                    issues_lists.append(inner)

    inspected = 0
    for lst in issues_lists:
        for item in lst:
            if isinstance(item, dict):
                assert "classified_issues" in item, (
                    f"issue item missing classified_issues field: {item}"
                )
                assert isinstance(item["classified_issues"], list)
                inspected += 1

    assert inspected > 0, (
        "no per-issue items inspected; synthetic-classifier fixture should "
        "yield at least one item per per-segment category"
    )


def test_validate_issue_carries_segment_uid(flask_client, tmp_reciter_dir):
    """Phase 6: every issue item carries a segment_uid (null for chapter-level)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "synthetic-classifier")

    res = flask_client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200
    body = res.get_json()

    found_any = False
    for v in body.values():
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    assert "segment_uid" in item, (
                        f"issue item must carry segment_uid (null allowed): {item}"
                    )
                    found_any = True
    assert found_any, "no per-issue items inspected"
