"""``/api/seg/validate`` producer → ``SegValidateResponse`` wire-model guard.

The route serializes the live validation result through ``SegValidateResponse``
(``model_validate`` then ``model_dump``). Every item model is
``ConfigDict(extra="forbid")``, so any key a producer emits that the model does
NOT declare raises ``extra_forbidden`` → HTTP 500 for the whole validation panel
(segments don't display). This drives the REAL ``_build_detail_lists`` producer
and asserts its output round-trips through the wire model — the regression guard
for that class.

Specifically covers the ``basmala_amin`` "missed Basmala" path, which carries a
``time`` field (via the ``{**first_seg}`` spread) that the model omitted — the
exact gap that 500'd a reciter on the dev Space. The fixture-driven snapshot
test never triggers that path (it needs ``deleted_basmala_chapters`` across many
chapters), so it lives here.
"""

from __future__ import annotations

from qua_shared.schemas.wire.seg import SegValidateResponse
from services.validation.detail import MISSED_BASMALA_FLAG_MIN_DELETED, _build_detail_lists

_DETAIL_LIST_KEYS = (
    "failed",
    "low_confidence",
    "low_confidence_v2",
    "cross_verse",
    "audio_bleeding",
    "repetitions",
    "muqattaat",
    "qalqala",
    "basmala_amin",
)


def test_missed_basmala_item_with_time_validates_through_response():
    """A "missed Basmala" candidate carries ``time`` (spread from ``first_seg``);
    ``SegValBasmalaAminItem`` must accept it so the validate response doesn't 500."""
    entries = [
        {
            "ref": "13",
            "segments": [
                {
                    "segment_uid": "ch13-first",
                    "matched_ref": "13:1:1-13:1:3",
                    "confidence": 1.0,
                    "time_start": 0,
                    "time_end": 6000,
                }
            ],
        }
    ]
    # >= MISSED_BASMALA_FLAG_MIN_DELETED stripped chapters enables the feature;
    # chapter 13 is NOT among them, so its first seg is a "possibly missed" flag.
    deleted = set(range(2, 2 + MISSED_BASMALA_FLAG_MIN_DELETED))

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(13, 1): 3},
        deleted_basmala_chapters=deleted,
    )

    items = detail["basmala_amin"]
    assert [it["segment_uid"] for it in items] == ["ch13-first"]
    assert items[0]["missed_basmala"] is True
    assert items[0]["time"] == "0:00-0:06"  # the field that used to 500

    resp = SegValidateResponse.model_validate({"basmala_amin": items})
    assert resp.basmala_amin[0].time == "0:00-0:06"
    # The field survives the wire dump (not silently dropped).
    dumped = resp.model_dump(mode="json", exclude_unset=True, by_alias=True)
    assert dumped["basmala_amin"][0]["time"] == "0:00-0:06"


def test_validate_response_accepts_multi_category_detail():
    """A spread of per-segment categories produced by the real engine all
    round-trip through ``SegValidateResponse`` — guards the whole item-model set
    against the ``extra_forbidden`` class, not just basmala_amin."""
    entries = [
        {
            "ref": "1",
            "segments": [
                {
                    "segment_uid": "s-failed",
                    "matched_ref": "",
                    "confidence": 1.0,
                    "time_start": 0,
                    "time_end": 1000,
                },
                {
                    "segment_uid": "s-lowconf",
                    "matched_ref": "1:2:1-1:2:1",
                    "confidence": 0.5,
                    "time_start": 1000,
                    "time_end": 2000,
                },
                {
                    "segment_uid": "s-cross",
                    "matched_ref": "1:5:7-1:6:1",
                    "confidence": 1.0,
                    "time_start": 2000,
                    "time_end": 3000,
                },
            ],
        }
    ]

    detail = _build_detail_lists(
        entries,
        is_by_ayah=False,
        word_counts={(1, 2): 4, (1, 5): 7, (1, 6): 3},
    )

    # The fixture is chosen to fire several distinct item shapes — assert they
    # actually populated so the round-trip below isn't vacuous over empty lists.
    fired = {k for k in _DETAIL_LIST_KEYS if detail[k]}
    assert {"failed", "low_confidence", "cross_verse"} <= fired, (
        f"expected categories to fire, got {fired}"
    )

    resp = SegValidateResponse.model_validate({k: detail[k] for k in _DETAIL_LIST_KEYS})
    total = sum(len(getattr(resp, k)) for k in _DETAIL_LIST_KEYS)
    assert total == sum(len(detail[k]) for k in _DETAIL_LIST_KEYS) > 0
