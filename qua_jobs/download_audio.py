#!/usr/bin/env python3
"""Download a recitation's source audio so release timestamps line up with it.

Shipped as a release asset. The release ``catalog.json`` pairs each reciter's
timestamps with the audio they were aligned against; for CDN reciters those are
direct per-chapter MP3s, but for playlist sources (YouTube / Google Drive) the
audio has to be fetched and re-encoded. This helper does that with the SAME
encoding the alignment used — **192 kbps CBR MP3, 44.1 kHz, mono** by default —
so a verse's ``[start_ms, end_ms]`` from the tier files lands on the right audio.

It reads ``catalog.json`` (the release-level array OR a single reciter's in-zip
``catalog.json``) and downloads one reciter's audio in one of three layouts:

  --format chapters   (default) one file per surah — ``001.mp3 … 114.mp3``.
                      Each file starts where that chapter starts, so the release
                      timestamps apply directly (offset 0), exactly like a CDN
                      by-surah reciter.

  --format ayah       one file per verse — ``001_001.mp3 … 114_006.mp3``. Each
                      verse is cut at its ``verse_timestamps.json.gz`` span, so
                      every file is a standalone ayah clip starting at 0 ms.
                      Needs the verse tier file (auto-located next to
                      ``catalog.json``, or passed with ``--timestamps``).

  --format original   the source files as published — one file per distinct
                      source URL (a YouTube video / Drive file may hold several
                      chapters). Timestamps are then relative to each chapter's
                      ``chapter_offsets_ms`` inside its file; a ``download_map.json``
                      records which file + offset each chapter maps to.

Both layouts re-encode to a controlled CBR so seeking is exact. Encoding params
are flags (``--bitrate``, ``--sample-rate``, ``--channels``) — changing them
does not move any timestamp (those are wall-clock ms), only the audio fidelity.

Requires ``yt-dlp`` and ``ffmpeg`` on PATH for YouTube/Drive sources; direct CDN
MP3s need neither. The helper tells you exactly what to install if a tool is
missing.

Examples::

    # one file per surah for a YouTube reciter (default layout)
    python download_audio.py catalog.json --reciter ibrahim_al_akhdar_drive

    # one standalone clip per verse (001_001.mp3 …)
    python download_audio.py catalog.json --reciter ibrahim_al_akhdar_drive \
        --format ayah

    # the original Drive files, plus a chapter→file+offset map
    python download_audio.py catalog.json --reciter mohammed_ayyub_drive \
        --format original --out-dir ./ayyub

    # just a few surahs, at a higher bitrate
    python download_audio.py catalog.json --reciter mohammed_burhaji_yt \
        --chapters 1,36,112 --bitrate 256k

    # list the reciters present in a catalog
    python download_audio.py catalog.json --list
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# yt-dlp's web client now needs a JS runtime to solve YouTube's nsig challenge;
# the android client returns pre-signed URLs that sidestep it (no JS runtime).
# Namespaced under youtube:, so it's inert for Drive / other sources.
_YTDLP_FORMAT = "bestaudio/best"
_YTDLP_EXTRACTOR_ARGS = "youtube:player_client=android,web"
_DIRECT_AUDIO_EXTS = frozenset({".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".wma", ".aac"})


# ---------------------------------------------------------------------------
# Friendly dependency checks.
# ---------------------------------------------------------------------------


class MissingTool(RuntimeError):
    """A required external tool is not installed — message says how to get it."""


def _require(tool: str, how: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise MissingTool(f"'{tool}' is required but not installed.\n  Install it with: {how}")
    return path


def _needs_ytdlp(url: str) -> bool:
    from urllib.parse import urlparse

    path = urlparse(url).path.split("?")[0].lower()
    return not any(path.endswith(ext) for ext in _DIRECT_AUDIO_EXTS)


# ---------------------------------------------------------------------------
# Catalog parsing.
# ---------------------------------------------------------------------------


def _reciters_in(catalog: dict | list) -> list[dict]:
    """Normalize a release-level catalog (``{recitations: [...]}`` or a bare
    list) or a single per-reciter catalog object to a list of reciter dicts."""
    if isinstance(catalog, dict) and "recitations" in catalog:
        return list(catalog["recitations"])
    if isinstance(catalog, list):
        return list(catalog)
    if isinstance(catalog, dict) and "slug" in catalog:
        return [catalog]
    raise ValueError("unrecognized catalog.json shape")


_VERSE_TS_NAMES = ("verse_timestamps.json.gz", "verse_timestamps.json")


def _locate_verse_timestamps(catalog_path: Path, explicit: Path | None) -> Path:
    """Resolve the verse tier file for ``--format ayah``.

    Uses ``--timestamps`` when given, else looks for ``verse_timestamps.json``
    (gzipped or plain) next to ``catalog.json`` — the in-zip layout.
    """
    if explicit:
        if not explicit.exists():
            raise ValueError(f"--timestamps {explicit} does not exist")
        return explicit
    for name in _VERSE_TS_NAMES:
        cand = catalog_path.parent / name
        if cand.exists():
            return cand
    raise ValueError(
        "--format ayah needs verse timestamps; none found next to "
        f"{catalog_path.name}. Unzip the reciter zip first, or pass --timestamps."
    )


def _load_verse_windows(path: Path) -> dict[str, list[tuple[int, int, int]]]:
    """Parse a verse tier file into ``{chapter: [(ayah, start_ms, end_ms), ...]}``.

    Reads gzipped or plain JSON. Occurrence rows are
    ``[ref,start_ms,end_ms,canonical,silence_after_ms]``; only the canonical row
    is cut so repeated/partial takes cannot overwrite the same ayah filename.
    Legacy keyed release tiers remain accepted.
    """
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    doc = json.loads(raw)
    by_chapter: dict[str, list[tuple[int, int, int]]] = {}
    rows = doc.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not row[3]:
                continue
            surah, ayah = row[0].split(":")
            by_chapter.setdefault(surah, []).append((int(ayah), int(row[1]), int(row[2])))
    else:
        for key, val in doc.items():
            if key.startswith("_"):
                continue
            surah, ayah = key.split(":")
            by_chapter.setdefault(surah, []).append((int(ayah), int(val[0]), int(val[1])))
    for chs in by_chapter.values():
        chs.sort()
    return by_chapter


def _pick_reciter(catalog: dict | list, slug: str | None) -> dict:
    recs = _reciters_in(catalog)
    if slug:
        for r in recs:
            if r.get("slug") == slug:
                return r
        raise ValueError(f"reciter {slug!r} not found in catalog")
    if len(recs) == 1:
        return recs[0]
    names = ", ".join(sorted(r.get("slug", "?") for r in recs))
    raise ValueError(
        f"catalog has {len(recs)} reciters — pass --reciter <slug>. Available: {names}"
    )


# ---------------------------------------------------------------------------
# Download + encode (mirrors the offline alignment pipeline's encode).
# ---------------------------------------------------------------------------


def _encode_cbr(src: Path, dest: Path, *, bitrate: str, sample_rate: str, channels: str) -> None:
    """Encode ``src`` to a controlled CBR MP3. ``-vn`` drops any embedded
    thumbnail (a cover-art stream otherwise yields a 0-byte mp3 on ffmpeg builds
    without a PNG encoder)."""
    ffmpeg = _require("ffmpeg", "https://ffmpeg.org/download.html (or `apt install ffmpeg`)")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(src),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-ar",
            sample_rate,
            "-ac",
            channels,
            "-f",
            "mp3",
            "-v",
            "error",
            str(dest),
        ],
        check=True,
    )


def _download_source(url: str, *, bitrate: str, sample_rate: str, channels: str) -> Path:
    """Fetch ``url`` and return a temp CBR MP3 path (caller owns cleanup)."""
    out = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name)
    if not _needs_ytdlp(url):
        import urllib.request

        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(url.split("?")[0]).suffix) as tf:
            raw = Path(tf.name)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, raw.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        try:
            _encode_cbr(raw, out, bitrate=bitrate, sample_rate=sample_rate, channels=channels)
        finally:
            raw.unlink(missing_ok=True)
        return out

    yt_dlp = _require("yt-dlp", "pip install yt-dlp")
    src_dir = Path(tempfile.mkdtemp(prefix="qua_src_"))
    try:
        proc = subprocess.run(
            [
                yt_dlp,
                "-f",
                _YTDLP_FORMAT,
                "--no-playlist",
                "--extractor-args",
                _YTDLP_EXTRACTOR_ARGS,
                "--retries",
                "5",
                "--fragment-retries",
                "5",
                "--retry-sleep",
                "5",
                "--socket-timeout",
                "30",
                "-o",
                str(src_dir / "src.%(ext)s"),
                "--force-overwrites",
                url,
            ],
            capture_output=True,
            text=True,
        )
        srcs = [p for p in src_dir.iterdir() if p.name.startswith("src.")]
        if proc.returncode != 0 or not srcs:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise RuntimeError(f"yt-dlp failed for {url}:\n{tail}")
        _encode_cbr(srcs[0], out, bitrate=bitrate, sample_rate=sample_rate, channels=channels)
        return out
    finally:
        shutil.rmtree(src_dir, ignore_errors=True)


def _trim_copy(src: Path, dest: Path, start_ms: int, end_ms: int | None) -> None:
    """Cut ``[start_ms, end_ms)`` from a CBR MP3 by frame copy (no re-encode).

    Input-seek + ``-c copy`` snaps to the MP3 frame at/just before ``start_ms``
    — the same frame the offline trim used, so the surviving timestamps stay
    aligned to within one frame (~26 ms). ``end_ms=None`` keeps to end-of-file.
    """
    ffmpeg = _require("ffmpeg", "https://ffmpeg.org/download.html (or `apt install ffmpeg`)")
    cmd = [ffmpeg, "-y", "-ss", f"{start_ms / 1000:.3f}"]
    if end_ms is not None:
        cmd += ["-to", f"{end_ms / 1000:.3f}"]
    cmd += [
        "-i",
        str(src),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-f",
        "mp3",
        "-v",
        "error",
        str(dest),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def _chapter_windows(
    offsets: dict[str, int], chapters: list[str]
) -> dict[str, tuple[int, int | None]]:
    """For each chapter on a SHARED source file, derive ``(start_ms, end_ms)``.

    Chapters on the same source are contiguous, so a chapter ends where the next
    (by offset) begins; the last runs to end-of-file (``None``). A chapter that
    owns its file starts at its offset (a trimmed lead-in) and runs to EOF.
    """
    by_off = sorted(chapters, key=lambda c: offsets.get(c, 0))
    windows: dict[str, tuple[int, int | None]] = {}
    for i, ch in enumerate(by_off):
        start = offsets.get(ch, 0)
        end = offsets.get(by_off[i + 1], None) if i + 1 < len(by_off) else None
        windows[ch] = (start, end)
    return windows


def download_reciter(
    reciter: dict,
    out_dir: Path,
    *,
    fmt: str,
    bitrate: str,
    sample_rate: str,
    channels: str,
    only: list[str] | None,
    verse_windows: dict[str, list[tuple[int, int, int]]] | None = None,
) -> dict:
    """Download one reciter's audio into ``out_dir``. Returns a download map.

    ``verse_windows`` is required for ``fmt == "ayah"`` — a
    ``{chapter: [(ayah, start_ms, end_ms), ...]}`` map from the verse tier file.
    """
    audio = reciter.get("audio") or {}
    chapter_urls: dict[str, str] = audio.get("chapter_urls") or {}
    offsets: dict[str, int] = audio.get("chapter_offsets_ms") or {}
    if not chapter_urls:
        raise ValueError(f"{reciter.get('slug')}: catalog has no chapter_urls")

    wanted = [c for c in chapter_urls if (not only or c in only)]
    if not wanted:
        raise ValueError(f"no matching chapters (requested {only})")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group the wanted chapters by their source URL (one download per source).
    by_url: dict[str, list[str]] = {}
    for ch in wanted:
        by_url.setdefault(chapter_urls[ch], []).append(ch)

    dl_map: dict[str, dict] = {}
    enc = dict(bitrate=bitrate, sample_rate=sample_rate, channels=channels)
    sources = sorted(by_url, key=lambda u: min(int(c) for c in by_url[u]))
    for idx, url in enumerate(sources, 1):
        chs = sorted(by_url[url], key=int)
        print(f"[{idx}/{len(sources)}] {url}  (chapters {', '.join(chs)})")
        src_mp3 = _download_source(url, **enc)
        try:
            if fmt == "original":
                name = f"source-{idx:02d}.mp3" if len(chs) > 1 else f"{int(chs[0]):03d}.mp3"
                shutil.copyfile(src_mp3, out_dir / name)
                for ch in chs:
                    dl_map[ch] = {"file": name, "offset_ms": int(offsets.get(ch, 0))}
                print(f"    -> {name}")
            elif fmt == "ayah":  # split each chapter into one offset-0 file per verse
                assert verse_windows is not None
                for ch in chs:
                    base = int(offsets.get(ch, 0))
                    verses = verse_windows.get(ch) or []
                    if not verses:
                        print(f"    !! no verse timestamps for chapter {ch} — skipped")
                        continue
                    for ayah, vs, ve in verses:
                        dest = out_dir / f"{int(ch):03d}_{ayah:03d}.mp3"
                        _trim_copy(src_mp3, dest, base + vs, base + ve)
                        dl_map[f"{ch}:{ayah}"] = {"file": dest.name, "offset_ms": 0}
                    print(f"    -> {int(ch):03d}_*.mp3  ({len(verses)} verse(s))")
            else:  # chapters: split each chapter to its own offset-0 file
                windows = _chapter_windows(offsets, chs)
                for ch in chs:
                    start, end = windows[ch]
                    dest = out_dir / f"{int(ch):03d}.mp3"
                    if len(chs) == 1 and start == 0:
                        shutil.copyfile(src_mp3, dest)
                    else:
                        _trim_copy(src_mp3, dest, start, end)
                    dl_map[ch] = {"file": dest.name, "offset_ms": 0}
                    print(f"    -> {dest.name}")
        finally:
            src_mp3.unlink(missing_ok=True)

    (out_dir / "download_map.json").write_text(
        json.dumps(
            {"slug": reciter.get("slug"), "format": fmt, "encode": enc, "chapters": dl_map},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return dl_map


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Download a recitation's source audio aligned to release timestamps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("catalog", type=Path, help="Path to catalog.json (release-level or per-reciter)")
    p.add_argument("--reciter", help="Reciter slug (required unless the catalog has one reciter)")
    p.add_argument("--list", action="store_true", help="List reciters in the catalog and exit")
    p.add_argument(
        "--format",
        choices=("chapters", "ayah", "original"),
        default="chapters",
        dest="fmt",
        help="Output layout (default: chapters = one file per surah; "
        "ayah = one file per verse; original = source files as published)",
    )
    p.add_argument("--out-dir", type=Path, help="Output directory (default: ./<slug>)")
    p.add_argument("--chapters", help="Comma-separated chapter subset (default: all)")
    p.add_argument(
        "--timestamps",
        type=Path,
        help="Verse tier file for --format ayah (default: verse_timestamps.json.gz "
        "next to catalog.json)",
    )
    p.add_argument("--bitrate", default="192k", help="CBR audio bitrate (default: 192k)")
    p.add_argument("--sample-rate", default="44100", help="Sample rate Hz (default: 44100)")
    p.add_argument("--channels", default="1", help="Channels: 1=mono, 2=stereo (default: 1)")
    args = p.parse_args(argv)

    if not args.catalog.exists():
        print(f"error: {args.catalog} does not exist", file=sys.stderr)
        return 2
    try:
        catalog = json.loads(args.catalog.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.catalog}: {exc}", file=sys.stderr)
        return 2

    if args.list:
        for r in _reciters_in(catalog):
            audio = r.get("audio") or {}
            n = len(audio.get("chapter_urls") or {})
            combined = " (combined sources)" if audio.get("chapter_offsets_ms") else ""
            print(f"  {r.get('slug'):42s} {n} chapter(s){combined}")
        return 0

    try:
        reciter = _pick_reciter(catalog, args.reciter)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    slug = reciter.get("slug") or "reciter"
    out_dir = args.out_dir or Path(slug)
    only = [c.strip() for c in args.chapters.split(",") if c.strip()] if args.chapters else None

    verse_windows = None
    if args.fmt == "ayah":
        try:
            ts_path = _locate_verse_timestamps(args.catalog, args.timestamps)
            verse_windows = _load_verse_windows(ts_path)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        dl_map = download_reciter(
            reciter,
            out_dir,
            fmt=args.fmt,
            bitrate=args.bitrate,
            sample_rate=args.sample_rate,
            channels=args.channels,
            only=only,
            verse_windows=verse_windows,
        )
    except MissingTool as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 3
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 4

    unit = "verse" if args.fmt == "ayah" else "chapter"
    print(f"\nDone — {len(dl_map)} {unit}(s) in {out_dir}/  (see download_map.json)")
    if args.fmt == "original":
        print("Timestamps are relative to each chapter's offset_ms within its file.")
    elif args.fmt == "ayah":
        print("Each NNN_VVV.mp3 is one verse starting at 0 ms.")
    else:
        print("Each NNN.mp3 starts at its chapter — release timestamps apply directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
