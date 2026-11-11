"""Has a chapter's upstream file changed since the delivery was aligned?

A source CDN can replace a chapter under the same URL. The bucket copy and every
segment / timestamp keep the original, while a GitHub release links the upstream
file, so a replaced recording ships timestamps that no longer match it.

``check_chapters`` range-reads each upstream head (``qua_shared.mp3_probe``) and
classifies it against the manifest's ``size_bytes`` / ``duration_sec`` /
``bitrate_kbps``:

  ok         size equals the manifest size (or nothing to compare)
  recording  duration differs by more than ``DURATION_TOL_S`` — a different recording
  reencode   same duration, different bitrate or sample rate
  tag_edit   size differs by under ``TAG_EDIT_BYTES``, same duration
  resized    size differs, same duration and bitrate
  gone       upstream answers 404 / 410
  error      network / parse failure

Only ``recording`` is blocking: the others keep the timestamps valid. Chapters
cut from a combined file (``source_url``), YouTube / Drive / SoundCloud sources
and non-http urls are not checked.
"""

from __future__ import annotations

import concurrent.futures as cf
import urllib.error
from dataclasses import dataclass, field

from qua_shared import mp3_probe

SKIP_HOSTS = ("youtube.com", "youtu.be", "drive.google.com", "docs.google.com", "soundcloud.com")
HEAD_BYTES = 16384
DURATION_TOL_S = 2.0
TAG_EDIT_BYTES = 65536
WORKERS = 16
BLOCKING = frozenset({"recording"})


@dataclass(frozen=True)
class ChapterCheck:
    chapter: str
    url: str
    verdict: str
    upstream: dict = field(default_factory=dict)
    aligned_duration_s: int | None = None
    aligned_size: int | None = None

    @property
    def blocking(self) -> bool:
        return self.verdict in BLOCKING

    def describe(self) -> str:
        now = self.upstream.get("duration_s")
        then = self.aligned_duration_s
        return f"ch{self.chapter} {self.verdict} ({then}s → {round(now) if now else '?'}s)"


def checkable(entry: dict) -> bool:
    url = entry.get("url") or ""
    return (
        url.startswith(("http://", "https://"))
        and not entry.get("source_url")
        and not any(h in url for h in SKIP_HOSTS)
    )


def probe(url: str) -> dict:
    """Upstream ``size`` / ``duration_s`` / ``kbps`` / ``sr`` from a head read, or
    ``{"gone": code}`` / ``{"error": message}`` after one retry."""
    for attempt in range(2):
        try:
            buf, total, tag = mp3_probe._fetch(mp3_probe.canonical_archive_url(url), HEAD_BYTES)
            break
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return {"gone": e.code}
            if attempt:
                return {"error": f"http {e.code}"}
        except Exception as e:  # noqa: BLE001 — retried once, then reported
            if attempt:
                return {"error": f"{type(e).__name__}: {e}"[:120]}
    foff, h = mp3_probe._first_frame(buf, mp3_probe._skip_id3(buf))
    if h is None:
        return {"size": total, "error": "no mp3 frame in head"}
    frames = mp3_probe._xing_frames(buf, foff, h)
    if frames:
        duration = frames * h["spf"] / h["sr"]
    elif total:
        duration = (total - tag) * 8 / (h["kbps"] * 1000)
    else:
        duration = None
    return {"size": total, "duration_s": duration, "kbps": h["kbps"], "sr": h["sr"]}


def classify(
    entry: dict, up: dict, *, expected_size: int | None = None, held: dict | None = None
) -> str:
    """Verdict for one chapter. ``expected_size`` stands in for a missing manifest
    ``size_bytes``; ``held`` is the bucket copy's ``kbps`` / ``sr`` when known."""
    held = held or {}
    if "gone" in up:
        return "gone"
    if "error" in up or up.get("size") is None:
        return "error"
    expected = entry.get("size_bytes") or expected_size
    if expected is None or up["size"] == expected:
        return "ok"
    duration, aligned = up.get("duration_s"), entry.get("duration_sec")
    if duration is not None and aligned is not None and abs(duration - aligned) > DURATION_TOL_S:
        return "recording"
    if abs(up["size"] - expected) < TAG_EDIT_BYTES:
        return "tag_edit"
    kbps = held.get("kbps") or entry.get("bitrate_kbps")
    if (kbps and up.get("kbps") and up["kbps"] != kbps) or (
        held.get("sr") and up.get("sr") and up["sr"] != held["sr"]
    ):
        return "reencode"
    return "resized"


def check_chapters(chapters: dict, *, workers: int = WORKERS) -> list[ChapterCheck]:
    """One :class:`ChapterCheck` per checkable by_surah manifest chapter."""
    todo = [
        (key, entry)
        for key, entry in chapters.items()
        if str(key).isdigit() and isinstance(entry, dict) and checkable(entry)
    ]
    with cf.ThreadPoolExecutor(workers) as ex:
        probes = list(ex.map(lambda ke: probe(ke[1]["url"]), todo))
    return [
        ChapterCheck(
            chapter=str(key),
            url=entry["url"],
            verdict=classify(entry, up),
            upstream=up,
            aligned_duration_s=entry.get("duration_sec"),
            aligned_size=entry.get("size_bytes"),
        )
        for (key, entry), up in zip(todo, probes, strict=True)
    ]


def blocking_changes(chapters: dict) -> list[ChapterCheck]:
    return [c for c in check_chapters(chapters) if c.blocking]
