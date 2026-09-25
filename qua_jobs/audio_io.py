"""Fetch / encode / cut / bake helpers shared by the acquire and split jobs.

Not an entrypoint. Every chapter the align pipeline persists goes through
``encode``: one controlled pass to the canonical chapter mp3 (192 kbps CBR,
44.1 kHz, source channel count), cover art stripped — the same bytes the
Inspector would serve, with the slim peaks blob baked beside it.

Fetching knows three kinds of source:

* Google Drive files — the ``drive.usercontent.google.com`` download endpoint
  with ``confirm=t`` (skips the large-file virus-scan interstitial);
* direct media URLs — a plain HTTP GET;
* everything else (YouTube, SoundCloud, archive.org pages) — yt-dlp. YouTube
  refuses Hugging Face IPs without a signed-in session: ``YTDLP_COOKIES`` (a
  Netscape cookies.txt, passed as a job secret) and optionally ``YTDLP_PROXY``
  are handed to yt-dlp when set.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from qua_shared.audio.sources import drive_file_id, needs_ytdlp

CANONICAL_BITRATE = "192k"
CANONICAL_SAMPLE_RATE = "44100"
USER_AGENT = "Mozilla/5.0 (quranic-universal-audio acquire)"
HTTP_TIMEOUT_S = 120
FFMPEG_TIMEOUT_S = 3 * 3600
YTDLP_FORMAT = "bestaudio/best"
_DRIVE_DOWNLOAD = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"
_MS = 1000

_cookie_file: str | None = None


def atomic_write(dest: Path, src: Path) -> None:
    """Move ``src`` onto ``dest`` via a same-directory temp so a reader never
    sees a half-written file on the bucket mount."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch(url: str, dest: Path) -> Path:
    """Download ``url`` next to ``dest``; returns the file actually written."""
    file_id = drive_file_id(url)
    if file_id:
        _http_get(_DRIVE_DOWNLOAD.format(id=file_id), dest, refuse_html=True)
        return dest
    if not needs_ytdlp(url):
        _http_get(url, dest, refuse_html=False)
        return dest
    return _ytdlp(url, dest)


def _http_get(url: str, dest: Path, *, refuse_html: bool) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if refuse_html and ctype.startswith("text/html"):
            raise RuntimeError(
                "Drive answered with a web page instead of the file — it is not shared "
                "publicly, or its download quota is exhausted (retry later)"
            )
        with dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)


def _ytdlp(url: str, dest: Path) -> Path:
    if shutil.which("yt-dlp") is None:
        raise RuntimeError("yt-dlp is not installed in the job image")
    template = str(dest.with_suffix("")) + ".%(ext)s"
    cmd = ["yt-dlp", "-f", YTDLP_FORMAT, "--no-playlist", "--no-progress"]
    cmd += ["--retries", "5", "--fragment-retries", "5", "--retry-sleep", "5"]
    cmd += ["--socket-timeout", "30", "--force-overwrites", "-o", template]
    cookies = _cookies_path()
    if cookies:
        cmd += ["--cookies", cookies]
    proxy = (os.environ.get("YTDLP_PROXY") or "").strip()
    if proxy:
        cmd += ["--proxy", proxy]
    proc = subprocess.run(
        [*cmd, url], capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S, check=False
    )
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
        if "confirm you" in tail and "not a bot" in tail:
            tail = (
                "YouTube refused the download (bot check) — set or refresh the "
                "INSPECTOR_YTDLP_COOKIES secret on the Space"
            )
        raise RuntimeError(f"yt-dlp failed: {tail[:400]}")
    written = sorted(dest.parent.glob(dest.with_suffix("").name + ".*"))
    written = [p for p in written if p.suffix not in (".part", ".ytdl")]
    if not written:
        raise RuntimeError("yt-dlp reported success but wrote no file")
    return written[0]


def _cookies_path() -> str | None:
    global _cookie_file
    if _cookie_file is not None:
        return _cookie_file or None
    text = os.environ.get("YTDLP_COOKIES") or ""
    if not text.strip():
        _cookie_file = ""
        return None
    fd, path = tempfile.mkstemp(prefix="ytdlp_cookies_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    _cookie_file = path
    return path


# ---------------------------------------------------------------------------
# Probe / encode / bake
# ---------------------------------------------------------------------------


def probe_channels(src: Path) -> int:
    out = _ffprobe(src, "stream=channels", stream="a:0")
    channels = int(out.splitlines()[0]) if out else 1
    return 2 if channels >= 2 else 1


def probe_duration_ms(src: Path) -> int | None:
    out = _ffprobe(src, "format=duration")
    try:
        return round(float(out.splitlines()[0]) * _MS)
    except (ValueError, IndexError):
        return None


def _ffprobe(src: Path, entries: str, *, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(src)]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()


def encode(
    src: Path,
    dest: Path,
    channels: int,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> None:
    """One canonical encode of ``src``, optionally only ``[start, end)``.

    The window uses input seeking, which is sample-accurate when transcoding
    (``-accurate_seek``, ffmpeg's default) and linear on a CBR source — the only
    kind the split job cuts from, since every slot file is this encode."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start_ms is not None:
        cmd += ["-ss", f"{start_ms / _MS:.3f}"]
    cmd += ["-i", str(src)]
    if end_ms is not None:
        cmd += ["-t", f"{(end_ms - (start_ms or 0)) / _MS:.3f}"]
    cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", CANONICAL_BITRATE]
    cmd += ["-ar", CANONICAL_SAMPLE_RATE, "-ac", str(channels), "-f", "mp3", str(dest)]
    subprocess.run(cmd, check=True, timeout=FFMPEG_TIMEOUT_S)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced an empty mp3")


def encode_pieces(pieces: list[tuple[Path, int, int]], dest: Path, channels: int) -> None:
    """One canonical encode of several ``(src, start_ms, end_ms)`` windows laid
    end to end — a surah uploaded in parts."""
    if len(pieces) == 1:
        src, start, end = pieces[0]
        encode(src, dest, channels, start_ms=start, end_ms=end)
        return
    layout = "mono" if channels == 1 else "stereo"
    cmd = ["ffmpeg", "-y", "-v", "error"]
    labels = []
    for i, (src, start, end) in enumerate(pieces):
        cmd += ["-ss", f"{start / _MS:.3f}", "-t", f"{(end - start) / _MS:.3f}", "-i", str(src)]
        labels.append(f"[a{i}]")
    fmt = f"aformat=sample_fmts=fltp:sample_rates={CANONICAL_SAMPLE_RATE}:channel_layouts={layout}"
    graph = ";".join(f"[{i}:a]{fmt}[a{i}]" for i in range(len(pieces)))
    graph += f";{''.join(labels)}concat=n={len(pieces)}:v=0:a=1[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-c:a", "libmp3lame"]
    cmd += ["-b:a", CANONICAL_BITRATE, "-ar", CANONICAL_SAMPLE_RATE, "-ac", str(channels)]
    cmd += ["-f", "mp3", str(dest)]
    subprocess.run(cmd, check=True, timeout=FFMPEG_TIMEOUT_S)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced an empty mp3")


def bake_peaks(mp3: Path) -> tuple[bytes, int]:
    """``(slim peaks blob, duration_ms)`` for a canonical chapter mp3."""
    from qua_shared.audio.peaks import compute_audio_peaks, pack_slim

    hd = compute_audio_peaks(str(mp3))
    if hd is None:
        raise RuntimeError("ffmpeg produced no peaks for the encoded audio")
    return pack_slim(hd), int(hd["duration_ms"])
