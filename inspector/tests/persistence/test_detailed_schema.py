"""detailed.json on-disk schema tests (MUST-2 — additive only)."""

from __future__ import annotations

import json

_HEADERS = {"Content-Type": "application/json", "Origin": "http://localhost"}

KNOWN_SEGMENT_FIELDS = {
    "segment_uid",
    "word_timings",  # optional historical/sample word-level review intervals
    "time_start",
    "time_end",
    "matched_ref",
    "confidence",
    # phonemes_asr is default-off post-Migration #5 (--with-phonemes-asr opt-in);
    # listed in the allow-list so legacy fixtures that still carry it parse cleanly.
    "phonemes_asr",
    "wrap_word_ranges",
    "qalqala_letter",  # persisted classifier optimisation (migrate_wip §2)
    "is_boundary_adj",  # persisted classifier optimisation (migrate_wip §2)
    "is_wasl",  # declared DetailedSegment field; persisted when truthy
    "flag",  # flagged-issue thread (SegmentFlag); persisted when present
    "ignored_categories",
    "ignored",
    "audio_url",
    # matched_text + has_repeated_words removed by Migration #5 — listed as
    # allowed strictly for legacy-fixture tolerance via the same allow-set.
    "matched_text",
    "has_repeated_words",
}


def _segments(detailed: dict) -> list[dict]:
    out = []
    for entry in detailed.get("entries", []):
        for s in entry.get("segments", []):
            out.append(s)
    return out


def test_detailed_json_round_trip_preserves_known_fields(
    load_fixture, tmp_reciter_dir, signed_in_client
):
    """Load fixture → save back → load → known fields equal."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")

    fixture = load_fixture("112-ikhlas")
    chapter = 112

    seg_payload = []
    for s in fixture["entries"][0]["segments"]:
        seg_payload.append(
            {
                "time_start": s["time_start"],
                "time_end": s["time_end"],
                "matched_ref": s["matched_ref"],
                "confidence": s["confidence"],
                "segment_uid": s["segment_uid"],
            }
        )
    payload = {"full_replace": True, "segments": seg_payload, "operations": []}

    res = client.post(
        f"/api/seg/save/{reciter}/{chapter}",
        data=json.dumps(payload),
        headers=_HEADERS,
    )
    assert res.status_code == 200

    out_path = tmp_reciter_dir.root / reciter / "detailed.json"
    saved = json.loads(out_path.read_text(encoding="utf-8"))

    saved_segs = saved["entries"][0]["segments"]
    assert len(saved_segs) == len(seg_payload)
    for orig, saved_seg in zip(fixture["entries"][0]["segments"], saved_segs, strict=True):
        for key in ("segment_uid", "time_start", "time_end", "matched_ref"):
            assert saved_seg.get(key) == orig.get(key), (
                f"field {key} drifted across save: orig={orig.get(key)!r} saved={saved_seg.get(key)!r}"
            )


def test_fixture_segments_use_only_known_keys(load_fixture):
    """Every field in the baseline fixture is recognized by KNOWN_SEGMENT_FIELDS.

    This validates fixture vocabulary alignment with the allow-set. It does
    NOT certify "no schema field removed" — that would require asserting
    every required ``DetailedSegment`` field is present in at least one seg.
    """
    fixture = load_fixture("112-ikhlas")
    for seg in _segments(fixture):
        unknown = set(seg.keys()) - KNOWN_SEGMENT_FIELDS
        assert not unknown, (
            f"unknown segment fields in fixture: {unknown}; update KNOWN_SEGMENT_FIELDS or "
            "remove them from the fixture"
        )


def test_detailed_json_excludes_classified_issues(load_fixture, tmp_reciter_dir, signed_in_client):
    """Validation responses carry classified_issues — but it must NOT be persisted to detailed.json (MAY-10)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for="test-user-1")
    client, _ = signed_in_client(hf_user_id="test-user-1", login="alice")

    res = client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200

    on_disk = json.loads(
        (tmp_reciter_dir.root / reciter / "detailed.json").read_text(encoding="utf-8")
    )
    for seg in _segments(on_disk):
        assert "classified_issues" not in seg, (
            "MUST-2 violation: classified_issues should never be persisted into detailed.json — "
            "it lives only on validation responses + history snapshots (MAY-10)."
        )
