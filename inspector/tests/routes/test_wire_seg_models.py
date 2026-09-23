"""Live-route regression net for the ``qua_shared.schemas.wire.seg`` models.

Every ``/api/seg/*`` response model is validated against the response the
route actually emits (Flask test client + the committed 112-ikhlas fixture),
and round-tripped: ``Model.model_validate(resp.json)`` must succeed AND
``model_dump(by_alias=True, exclude_none=True)`` must reproduce the exact
top-level key set of the response. This is the Phase-5 safety net — when the
routes are switched to emit these models, this test guards against the model
dropping or renaming a field the wire carries today.

Request-body models are exercised against representative inline fixtures
matching what the FE payload builders send.
"""

from __future__ import annotations

import json

import pytest

from qua_shared.schemas.wire import seg as wire


def _keys(d: dict) -> set[str]:
    return set(d.keys())


def _assert_roundtrip(model_cls, payload: dict) -> None:
    """Validate + dump-back: the model reproduces the payload's top-level keys.

    ``exclude_none`` mirrors the on-wire shape (routes drop unset optionals);
    every key the route emitted must round-trip back, and the model must not
    add keys the wire didn't carry.
    """
    obj = model_cls.model_validate(payload)
    dumped = obj.model_dump(by_alias=True, exclude_none=True)
    assert _keys(dumped) == _keys(payload), (
        f"{model_cls.__name__} key drift: "
        f"only-in-model={_keys(dumped) - _keys(payload)!r}, "
        f"only-in-wire={_keys(payload) - _keys(dumped)!r}"
    )


@pytest.fixture
def installed(flask_client, tmp_reciter_dir):
    """Install the 112-ikhlas fixture and yield (client, reciter)."""
    reciter = "fixture_reciter"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    return flask_client, reciter


# ---------------------------------------------------------------------------
# Read endpoints — validate against the live response
# ---------------------------------------------------------------------------


def test_config_model_matches_live_response(flask_client):
    res = flask_client.get("/api/seg/config")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    _assert_roundtrip(wire.SegConfigResponse, body)


def test_reciters_model_matches_live_response(installed):
    client, _reciter = installed
    res = client.get("/api/seg/reciters")
    assert res.status_code == 200, res.get_data(as_text=True)
    rows = res.get_json()
    assert isinstance(rows, list) and rows, "expected at least one reciter row"
    # RootModel over the list validates the whole array.
    parsed = wire.SegRecitersResponse.model_validate(rows)
    dumped = parsed.model_dump(by_alias=True, exclude_none=True)
    assert dumped == rows
    # Each row reproduces its key set (state + visibility are route-emitted).
    for row in rows:
        _assert_roundtrip(wire.SegReciter, row)
    assert {"state", "visibility"} <= _keys(rows[0]), "route must emit state + visibility"


def test_data_model_matches_live_response(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/data/{reciter}/112")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    _assert_roundtrip(wire.SegDataResponse, body)
    # Nested segment + summary round-trip too.
    for s in body["segments"]:
        _assert_roundtrip(wire.SegDataSegment, s)
    _assert_roundtrip(wire.SegmentsChapterSummary, body["summary"])


def test_all_model_matches_live_response(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/all/{reciter}")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    _assert_roundtrip(wire.SegAllResponse, body)
    for s in body["segments"]:
        _assert_roundtrip(wire.SegAllSegment, s)
    # /all segs carry segment_uid + entry_ref and OMIT audio_url.
    first = body["segments"][0]
    assert {"segment_uid", "entry_ref", "chapter"} <= _keys(first)
    assert "audio_url" not in first


def test_segment_routes_expose_existing_word_timings(flask_client, tmp_reciter_dir):
    reciter = "word_timing_sample"
    tmp_reciter_dir.install(reciter, "112-ikhlas")
    detailed_path = tmp_reciter_dir.root / reciter / "detailed.json"
    detailed = json.loads(detailed_path.read_text(encoding="utf-8"))
    first = detailed["entries"][0]["segments"][0]
    first["word_timings"] = [
        {
            "word": "قُلْ",
            "location": "112:1:1",
            "start_ms": first["time_start"],
            "end_ms": first["time_end"],
        }
    ]
    detailed_path.write_text(
        json.dumps(detailed, ensure_ascii=False),
        encoding="utf-8",
    )

    chapter = flask_client.get(f"/api/seg/data/{reciter}/112")
    assert chapter.status_code == 200, chapter.get_data(as_text=True)
    chapter_timing = chapter.get_json()["segments"][0]["word_timings"][0]
    assert chapter_timing["location"] == "112:1:1"

    all_rows = flask_client.get(f"/api/seg/all/{reciter}")
    assert all_rows.status_code == 200, all_rows.get_data(as_text=True)
    all_timing = all_rows.get_json()["segments"][0]["word_timings"][0]
    assert all_timing["word"] == "قُلْ"


def test_validate_model_matches_live_response(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/validate/{reciter}")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    _assert_roundtrip(wire.SegValidateResponse, body)
    # The route emits category_counts + stats + split_group_index.
    assert {"category_counts", "stats", "split_group_index"} <= _keys(body)


def test_validate_items_carry_classified_issues(installed):
    """The per-item ``classified_issues`` field is emitted on every detail item
    and round-trips through the variant models."""
    client, reciter = installed
    body = client.get(f"/api/seg/validate/{reciter}").get_json()

    # 112-ikhlas reliably produces low_confidence + qalqala items.
    assert body["low_confidence"], "fixture expected to yield a low_confidence item"
    assert body["qalqala"], "fixture expected to yield qalqala items"

    for item in body["low_confidence"]:
        _assert_roundtrip(wire.SegValLowConfidenceItem, item)
        assert "classified_issues" in item
    for item in body["qalqala"]:
        _assert_roundtrip(wire.SegValQalqalaItem, item)
        assert {"qalqala_letter", "end_of_verse"} <= _keys(item)


def test_validate_any_item_union_accepts_every_variant(installed):
    """The SegValAnyItem union validates every live item regardless of category."""
    client, reciter = installed
    body = client.get(f"/api/seg/validate/{reciter}").get_json()

    category_keys = [
        "errors",
        "structural_errors",
        "missing_verses",
        "missing_words",
        "failed",
        "low_confidence",
        "low_confidence_v2",
        "boundary_adj",
        "cross_verse",
        "audio_bleeding",
        "repetitions",
        "muqattaat",
        "qalqala",
        "basmala_amin",
    ]
    seen = 0
    for cat in category_keys:
        for item in body.get(cat) or []:
            # RootModel union must accept the item without an explicit category.
            wire.SegValAnyItem.model_validate(item)
            seen += 1
    assert seen > 0, "fixture should yield at least one validation item"


def test_peaks_model_matches_live_response(installed):
    client, reciter = installed
    res = client.get(f"/api/seg/peaks/{reciter}?chapters=112")
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    # The 112-ikhlas fixture ships no baked peaks file, so `peaks` is empty —
    # the envelope shape (peaks map + complete flag) still round-trips.
    _assert_roundtrip(wire.SegPeaksResponse, body)
    assert body["complete"] is True


# ---------------------------------------------------------------------------
# Edit endpoints — validate against the live response (save / undo)
# ---------------------------------------------------------------------------


def test_save_response_model_matches_live_response(signed_in_client, tmp_reciter_dir):
    """A real same-origin patch save returns ``{"ok": true}`` — and only that."""
    reciter = "fixture_reciter"
    client, user = signed_in_client(role="contributor")
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for=user["hf_user_id"])

    payload = wire.SegSaveRequest(
        segments=[
            {"index": 0, "segment_uid": "x", "matched_ref": "112:1:1-112:1:4", "confidence": 1.0}
        ],
        operations=[],
    )
    res = client.post(
        f"/api/seg/save/{reciter}/112",
        json=payload.model_dump(exclude_none=True),
        headers={"Origin": "http://localhost"},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    _assert_roundtrip(wire.SegSaveResponse, body)
    assert body == {"ok": True}


def test_undo_response_model_carries_operations_reversed(signed_in_client, tmp_reciter_dir):
    """Undo returns ``{ok, operations_reversed}``. Drive a real save first to
    produce a batch, then undo it."""
    reciter = "fixture_reciter"
    client, user = signed_in_client(role="contributor")
    tmp_reciter_dir.install(reciter, "112-ikhlas", under_review_for=user["hf_user_id"])

    save_res = client.post(
        f"/api/seg/save/{reciter}/112",
        json={
            "segments": [
                {
                    "index": 0,
                    "segment_uid": "x",
                    "matched_ref": "112:1:1-112:1:4",
                    "confidence": 1.0,
                }
            ],
            "operations": [
                {
                    "op_id": "op-1",
                    "op_type": "edit_reference",
                    "command": {"type": "edit_reference"},
                    "targets_before": [],
                    "targets_after": [],
                }
            ],
        },
        headers={"Origin": "http://localhost"},
    )
    assert save_res.status_code == 200, save_res.get_data(as_text=True)

    # Recover the batch_id from the persisted edit-history.
    hist = client.get(f"/api/seg/edit-history/{reciter}").get_json()
    batches = hist.get("batches") or []
    assert batches, "expected a persisted batch after save"
    batch_id = batches[0]["batch_id"]

    undo_res = client.post(
        f"/api/seg/undo-batch/{reciter}",
        json=wire.SegUndoBatchRequest(batch_id=batch_id).model_dump(),
        headers={"Origin": "http://localhost"},
    )
    assert undo_res.status_code == 200, undo_res.get_data(as_text=True)
    body = undo_res.get_json()
    _assert_roundtrip(wire.SegUndoResponse, body)
    assert "operations_reversed" in body


# ---------------------------------------------------------------------------
# Request-body models — representative inline fixtures (FE payload shapes)
# ---------------------------------------------------------------------------


def test_save_request_model_validates_patch_and_full_payloads():
    patch_body = {
        "segments": [{"index": 0, "segment_uid": "u", "matched_ref": "112:1:1", "confidence": 0.9}],
        "operations": [],
    }
    _assert_roundtrip(wire.SegSaveRequest, patch_body)

    full_body = {
        "full_replace": True,
        "segments": [
            {
                "time_start": 100,
                "time_end": 200,
                "matched_ref": "112:1:1-112:1:4",
                "confidence": 1.0,
                "audio_url": "https://cdn/112.mp3",
            }
        ],
        "operations": [{"op_id": "o1", "op_type": "split_segment"}],
    }
    _assert_roundtrip(wire.SegSaveRequest, full_body)
    # The full-payload segment matches SegSaveFullSegment.
    _assert_roundtrip(wire.SegSaveFullSegment, full_body["segments"][0])


def test_undo_request_models_validate():
    _assert_roundtrip(wire.SegUndoBatchRequest, {"batch_id": "b1"})
    _assert_roundtrip(wire.SegUndoOpsRequest, {"batch_id": "b1", "op_ids": ["o1", "o2"]})


def test_segment_peaks_request_model_validates_inline_fixture():
    body = {
        "segments": [
            {"url": "https://cdn/112.mp3", "start_ms": 4700, "end_ms": 7700},
            {
                "url": "https://cdn/112.mp3",
                "start_ms": 8020,
                "end_ms": 10460,
                "chapter": 112,
                "pad_ms": 200,
                "bps": 10,
            },
        ]
    }
    _assert_roundtrip(wire.SegSegmentPeaksRequest, body)
    for item in body["segments"]:
        _assert_roundtrip(wire.SegSegmentPeaksRequestItem, item)


def test_segment_peaks_response_model_validates_inline_fixture():
    """compute_segment_peaks shape: {schema_version, start_ms, end_ms,
    duration_ms, peaks[[min,max]]} — keyed by ``url:start:end``."""
    body = {
        "peaks": {
            "https://cdn/112.mp3:4700:7700": {
                "schema_version": 3,
                "start_ms": 4700,
                "end_ms": 7700,
                "duration_ms": 3000,
                "peaks": [[-0.5, 0.6], [-0.1, 0.2]],
            }
        }
    }
    _assert_roundtrip(wire.SegSegmentPeaksResponse, body)
    _assert_roundtrip(wire.SegSegmentPeaks, body["peaks"]["https://cdn/112.mp3:4700:7700"])


def test_slim_peaks_envelope_model_validates_inline_fixture():
    """The /peaks per-URL value is the slim int8 envelope (NOT nested floats)."""
    envelope = {
        "schema_version": 3,
        "duration_ms": 60000,
        "bps": 10,
        "q": "int8",
        "n": 600,
        "peaks_b64": "AAAA",
    }
    _assert_roundtrip(wire.SegSlimPeaks, envelope)
    full = {"peaks": {"https://cdn/112.mp3": envelope}, "complete": True}
    _assert_roundtrip(wire.SegPeaksResponse, full)
