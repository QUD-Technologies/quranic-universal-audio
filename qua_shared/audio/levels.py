"""Chapter loudness levels — whole-decibel RMS per 20 ms frame, and their packed blob.

Baked next to the peaks by the acquire and split HF Jobs
(``reciters/<slug>/levels/<ch>.json.gz``) so the align pipeline can measure how
long a reciter was silent at a join without decoding audio in the Inspector.
The frame and the level scale match the boundary-head lab's envelope
(``hidden_pause.transcribe.rms_db``), whose floors the pipeline reuses.

Blob: one gzip member holding ``{schema_version, frame_ms, n, levels_b64}``,
``levels_b64`` the int8 dB levels.
"""

from __future__ import annotations

import base64
import gzip
import json
import subprocess

import numpy as np

LEVELS_SCHEMA_VERSION = 1
FRAME_MS = 20
SAMPLE_RATE = 16000
#: Keeps a digital-silence frame finite: -100 dB.
RMS_FLOOR = 1e-5
FFMPEG_TIMEOUT = 600

_FRAME = SAMPLE_RATE * FRAME_MS // 1000
_PCM_SCALE = 32768.0


def compute_levels(audio_path: str, *, timeout: float = FFMPEG_TIMEOUT) -> np.ndarray:
    """int8 dB level per ``FRAME_MS`` frame of ``audio_path``; raises when ffmpeg fails."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            audio_path,
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],  # fmt: skip
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    pcm = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / _PCM_SCALE
    frames = len(pcm) // _FRAME
    if frames == 0:
        raise RuntimeError(f"no audio frames decoded from {audio_path}")
    blocks = pcm[: frames * _FRAME].reshape(frames, _FRAME)
    db = 20.0 * np.log10(np.sqrt(np.mean(blocks * blocks, axis=1)) + RMS_FLOOR)
    return np.clip(np.round(db), -128, 127).astype(np.int8)


def pack(levels: np.ndarray) -> bytes:
    doc = {
        "schema_version": LEVELS_SCHEMA_VERSION,
        "frame_ms": FRAME_MS,
        "n": int(levels.shape[0]),
        "levels_b64": base64.b64encode(levels.astype(np.int8).tobytes()).decode("ascii"),
    }
    return gzip.compress(
        json.dumps(doc, separators=(",", ":")).encode("utf-8"), compresslevel=6, mtime=0
    )


def unpack(blob: bytes) -> np.ndarray:
    doc = json.loads(gzip.decompress(blob))
    if doc.get("schema_version") != LEVELS_SCHEMA_VERSION or doc.get("frame_ms") != FRAME_MS:
        raise ValueError(
            f"unsupported levels blob: {doc.get('schema_version')}/{doc.get('frame_ms')}"
        )
    return np.frombuffer(base64.b64decode(doc["levels_b64"]), dtype=np.int8).astype(np.int16)
