"""How long the reciter was silent at a join, from the chapter's baked loudness levels.

Levels are ``qua_shared.audio.levels`` blobs (``reciters/<slug>/levels/<ch>.json.gz``,
20 ms frames). Two silences are measured around a join's cursor, as the boundary-head
lab measures them (``hidden_pause.report``):

* ``floor_ms`` — the longest run of frames within ``FLOOR_DB`` of the chapter's noise
  floor (its ``FLOOR_PERCENTILE`` level). Soft speech and reverb sit under the speech
  level but never reach the floor, so this is the stop evidence.
* ``silence_ms`` / ``dip_db`` — the longest run ``SILENCE_DB`` under the segment's
  speech level (its ``SPEECH_PERCENTILE`` level), and how deep the quietest frame dips.

Each run is sought within ``WINDOW_MS`` of the cursor and followed past it while the
frames stay quiet, up to ``REACH_FRAMES``: the aligner's word timings abut, so the
cursor marks the join, not the silence. ``at_ms`` is the middle of the floor run (else
the speech-level run), where a WAQF split belongs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from qua_shared.audio import levels as levels_blob
from services.storage.hf_bucket import StorageNotFound, get_backend

log = logging.getLogger("inspector")

FRAME_MS = levels_blob.FRAME_MS
FLOOR_PERCENTILE = 0.02
FLOOR_DB = 10
SPEECH_PERCENTILE = 0.9
SILENCE_DB = 10
WINDOW_MS = 300
REACH_FRAMES = 25


@dataclass(frozen=True)
class Silence:
    floor_ms: int
    silence_ms: int
    dip_db: int
    at_ms: int


def levels_path(slug: str, chapter: int) -> str:
    return f"reciters/{slug}/levels/{chapter}.json.gz"


class ChapterLevels:
    def __init__(self, levels: np.ndarray) -> None:
        self._lv = levels
        self.floor = int(np.sort(levels)[int(FLOOR_PERCENTILE * (len(levels) - 1))])

    @classmethod
    def load(cls, slug: str, chapter: int) -> ChapterLevels | None:
        """The chapter's levels, or ``None`` when none were baked."""
        try:
            blob = get_backend().read_bytes(levels_path(slug, chapter))
        except (StorageNotFound, FileNotFoundError):
            return None
        levels = levels_blob.unpack(blob)
        return cls(levels) if len(levels) else None

    def measure(self, cursor_ms: int, clip_start_ms: int, clip_end_ms: int) -> Silence:
        lv = self._lv
        clip = lv[
            clip_start_ms // FRAME_MS : max(clip_end_ms // FRAME_MS, clip_start_ms // FRAME_MS + 1)
        ]
        speech = int(np.sort(clip)[int(SPEECH_PERCENTILE * (len(clip) - 1))]) if len(clip) else 0
        at = cursor_ms // FRAME_MS
        half = WINDOW_MS // FRAME_MS
        a, b = max(at - half, 0), min(at + half, len(lv))
        floor_run = self._run(a, b, self.floor + FLOOR_DB)
        speech_run = self._run(a, b, speech - SILENCE_DB)
        dip = speech - int(lv[a:b].min()) if b > a else 0
        mid = floor_run or speech_run
        return Silence(
            floor_ms=_span(floor_run) * FRAME_MS,
            silence_ms=_span(speech_run) * FRAME_MS,
            dip_db=dip,
            at_ms=(mid[0] + mid[1]) * FRAME_MS // 2 if mid else cursor_ms,
        )

    def _run(self, a: int, b: int, thr: int) -> tuple[int, int] | None:
        """``[lo, hi)`` of the longest run ``<= thr`` in ``[a, b)``, followed outward."""
        lv = self._lv
        quiet = np.concatenate([[False], lv[a:b] <= thr, [False]])
        edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
        if not len(edges):
            return None
        starts, ends = edges[::2], edges[1::2]
        k = int(np.argmax(ends - starts))
        lo, hi = a + int(starts[k]), a + int(ends[k])
        while lo > 0 and a - lo < REACH_FRAMES and lv[lo - 1] <= thr:
            lo -= 1
        while hi < len(lv) and hi - b < REACH_FRAMES and lv[hi] <= thr:
            hi += 1
        return lo, hi


def _span(run: tuple[int, int] | None) -> int:
    return run[1] - run[0] if run else 0
