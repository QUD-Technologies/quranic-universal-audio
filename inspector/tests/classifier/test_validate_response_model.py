"""``/api/seg/validate`` producer → ``SegValidateResponse`` wire-model guard.

The route serializes the live validation result through ``SegValidateResponse``
(``model_validate`` then ``model_dump``). Every item model is
``ConfigDict(extra="forbid")``, so any key a producer emits that the model does
NOT declare raises ``extra_forbidden`` → HTTP 500 for the whole validation panel
(segments don't display). This drives the REAL ``_build_detail_lists`` producer
and asserts its output round-trips through the wire model — the regression guard
for that class.
"""

from __future__ import annotations

from qua_shared.schemas.wire.seg import SegValidateResponse
from services.validation.detail import _build_detail_lists

_DETAIL_LIST_KEYS = (
    "failed",
    "low_confidence",
    "low_confidence_v2",
    "cross_verse",
    "audio_bleeding",
    "repetitions",
    "muqattaat",
    "qalqala",
)


def test_validate_response_accepts_multi_category_detail():
    """A spread of per-segment categories produced by the real engine all
    round-trip through ``SegValidateResponse`` — guards the whole item-model set
    against the ``extra_forbidden`` class."""
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
