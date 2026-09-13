"""Run parameters + environment for the native align pipeline.

Every knob the aligner Space is asked for lives here, once. The defaults are the
Katana-parity extraction settings the owner chose: the Large model, 100 ms pads
on both sides, a 50 ms silence floor, matcher/thresholds left to the Space.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

DEFAULT_ALIGNER_URL = "https://hetchyy-quranic-universal-aligner-dev.hf.space"

MODEL_LARGE = "Large"
DEVICE_GPU = "GPU"
DEVICE_CPU = "CPU"
PAD_LEFT_MS = 100
PAD_RIGHT_MS = 100
MIN_SILENCE_FLOOR_MS = 50

#: What ``_meta.asr_model`` records per aligner model name. ``Large`` is the
#: same ``hetchyy/r7`` checkpoint the Katana extraction used.
ASR_MODEL_IDS = {"Large": "hetchyy/r7", "Base": "aligner:Base"}
VAD_MODEL_ID = "hetchyy/qua-bnd-trio-head"
ALIGN_WORKERS = 16


@dataclass(frozen=True)
class AlignParams:
    model_name: str = MODEL_LARGE
    pad_left_ms: int = PAD_LEFT_MS
    pad_right_ms: int = PAD_RIGHT_MS
    min_silence_floor_ms: int = MIN_SILENCE_FLOOR_MS
    #: SDK riwayah slug (``hafs``/``warsh``/…) the aligner matches against.
    riwayah: str = "hafs"
    #: Lane the batch STARTS on — ``GPU`` (ZeroGPU, falls back to CPU when the
    #: quota runs out) or ``CPU`` (the Space's CPU worker pool, no quota).
    device: str = DEVICE_GPU

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None) -> AlignParams:
        data = json.loads(raw) if raw else {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def batch_body(self, *, device: str | None = None) -> dict:
        """The ``POST /api/v1/batches`` body for an alignment-only batch.

        ``device`` overrides the run's starting lane — the align stage passes the
        live one so a GPU→CPU fallback recreates the batch on CPU."""
        return {
            "model_name": self.model_name,
            "device": device or self.device,
            "riwayah": self.riwayah,
            "pad_left_ms": self.pad_left_ms,
            "pad_right_ms": self.pad_right_ms,
            "min_silence_floor_ms": self.min_silence_floor_ms,
            "include_word_timestamps": False,
            "include_merge_groups": True,
            "discard_session": True,
        }


def aligner_url() -> str:
    return (os.environ.get("INSPECTOR_ALIGNER_URL") or DEFAULT_ALIGNER_URL).rstrip("/")


def extraction_secret() -> str:
    return (os.environ.get("INSPECTOR_EXTRACTION_SECRET") or "").strip()


def hf_token() -> str:
    token = os.environ.get("INSPECTOR_HF_TOKEN") or os.environ.get("HF_TOKEN") or ""
    if not token:
        from huggingface_hub import get_token

        token = get_token() or ""
    return token.strip()


def align_concurrency(pending: int) -> int:
    """Rolling HTTP worker count; resource admission belongs to the aligner.

    Sixteen keeps bucket reads and network setup moving without reproducing the
    old 114-request burst. The aligner independently schedules every item by
    caller and may run fewer of them on either GPU or CPU.
    """
    override = (os.environ.get("INSPECTOR_ALIGN_CONCURRENCY") or "").strip()
    if override.isdigit() and int(override) > 0:
        return min(int(override), pending)
    return max(1, min(ALIGN_WORKERS, pending))


def pipeline_enabled() -> bool:
    return os.environ.get("INSPECTOR_ALIGN_PIPELINE", "0") == "1"


def keep_staging() -> bool:
    return os.environ.get("INSPECTOR_ALIGN_KEEP_STAGING", "0") == "1"


def missing_config() -> list[str]:
    """Names of the env vars a run cannot start without."""
    missing = []
    if not extraction_secret():
        missing.append("INSPECTOR_EXTRACTION_SECRET")
    if not hf_token():
        missing.append("INSPECTOR_HF_TOKEN")
    return missing
