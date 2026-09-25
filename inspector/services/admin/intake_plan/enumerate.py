"""An intake source → the media entries it is made of (no audio is fetched).

* ``links`` — the contributor's per-chapter URLs. Chapters are already known;
  links sharing one URL become one combined entry.
* Google Drive — a folder is listed through ``drive.py``; a single file is one
  entry.
* Everything else (YouTube playlist or video, SoundCloud set, archive.org item,
  a direct media URL) — yt-dlp's flat extraction. yt-dlp is imported lazily so
  the app boots without it; enumeration then fails with a clear message.

YouTube *listing* works from Hugging Face infrastructure; YouTube *downloads*
need cookies (see ``qua_jobs/acquire_audio.py``). A playlist that yt-dlp reports
as longer than what it returned (an old yt-dlp, a region lock) is refused rather
than silently planning a 100-of-114 mushaf.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from qua_shared.audio.sources import needs_ytdlp
from qua_shared.schemas import IntakeSource

from . import drive

log = logging.getLogger("inspector")

#: Hosts whose flat listing omits titles, so each entry is resolved on its own.
_RESOLVE_TITLE_HOSTS = ("soundcloud",)
_RESOLVE_WORKERS = 8
_UNAVAILABLE_TITLES = ("[deleted video]", "[private video]", "[unavailable video]")
_HOSTS = (
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("drive", ("drive.google.com", "docs.google.com")),
    ("soundcloud", ("soundcloud.com",)),
    ("archive", ("archive.org",)),
)


class EnumerationError(RuntimeError):
    """The source could not be listed — the message is shown to the owner."""


@dataclass
class RawEntry:
    url: str
    title: str = ""
    index: int | None = None
    duration_sec: float | None = None
    #: Only for ``links``: the chapters the contributor already assigned.
    chapters: list[int] = field(default_factory=list)
    unavailable: bool = False


@dataclass
class Listing:
    host: str
    entries: list[RawEntry]
    source_url: str | None = None
    uploader: str | None = None
    uploader_url: str | None = None


def host_of(url: str) -> str:
    netloc = (urlparse(url).netloc or "").lower().split(":")[0]
    for host, domains in _HOSTS:
        if any(netloc == d or netloc.endswith("." + d) for d in domains):
            return host
    return "other"


def enumerate_source(source: IntakeSource) -> Listing:
    if source.method == "links":
        return _from_links(source)
    url = (source.playlist_url or "").strip()
    if not url:
        raise EnumerationError("the submission has no playlist URL")
    host = host_of(url)
    if host == "drive":
        return _from_drive(url)
    return _from_ytdlp(url, host)


def _from_links(source: IntakeSource) -> Listing:
    by_url: dict[str, RawEntry] = {}
    for link in sorted(source.links, key=lambda ln: ln.chapter):
        entry = by_url.setdefault(link.url, RawEntry(url=link.url, title=_leaf(link.url)))
        if link.chapter not in entry.chapters:
            entry.chapters.append(link.chapter)
    if not by_url:
        raise EnumerationError("the submission lists no links")
    return Listing(host="links", entries=list(by_url.values()))


def _from_drive(url: str) -> Listing:
    fid = drive.folder_id(url)
    try:
        if fid is None:
            file_id = drive.file_id(url)
            if file_id is None:
                raise EnumerationError(f"not a Drive folder or file link: {url}")
            entries = [RawEntry(url=drive.file_url(file_id), title=file_id, index=1)]
        else:
            files = drive.list_folder(fid)
            entries = [RawEntry(url=f.url, title=f.name, index=i) for i, f in enumerate(files, 1)]
    except drive.DriveError as exc:
        raise EnumerationError(str(exc)) from exc
    if not entries:
        raise EnumerationError("the Drive folder holds no audio files")
    return Listing(host="drive", entries=entries, source_url=url)


def _ytdlp() -> Any:
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover — the image installs it
        raise EnumerationError("yt-dlp is not installed on this server") from exc
    return yt_dlp


def _from_ytdlp(url: str, host: str) -> Listing:
    yt_dlp = _ytdlp()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises its own DownloadError tree
        if host == "youtube":
            return _single_youtube_fallback(url, exc)
        raise EnumerationError(f"could not list {url}: {_short(exc)}") from exc
    raw = info.get("entries")
    if raw is None:  # a single media URL, not a playlist
        entries = [_entry(info, 1, fallback_url=url)]
    else:
        entries = [_entry(e or {}, i) for i, e in enumerate(raw, 1)]
    expected = info.get("playlist_count")
    if isinstance(expected, int) and expected > len(entries):
        raise EnumerationError(
            f"the playlist reports {expected} entries but only {len(entries)} were listed — "
            "retry, or split the playlist"
        )
    if host in _RESOLVE_TITLE_HOSTS:
        entries = _resolve_titles(entries)
    if not entries:
        raise EnumerationError("the playlist is empty")
    return Listing(
        host=host,
        entries=entries,
        source_url=url,
        uploader=info.get("channel") or info.get("uploader"),
        uploader_url=info.get("channel_url") or info.get("uploader_url"),
    )


def _entry(e: dict, index: int, *, fallback_url: str | None = None) -> RawEntry:
    title = (e.get("title") or "").strip()
    direct = e.get("url") or ""
    # A listing entry's own media URL (archive.org files) beats the page it sits
    # on, which every entry of the item shares.
    url = direct if direct and not needs_ytdlp(direct) else e.get("webpage_url") or direct
    url = url or fallback_url or ""
    if e.get("ie_key") == "Youtube" and e.get("id"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    duration = e.get("duration")
    return RawEntry(
        url=url,
        title=title,
        index=e.get("playlist_index") or index,
        duration_sec=float(duration) if isinstance(duration, (int, float)) else None,
        unavailable=not title or title.lower() in _UNAVAILABLE_TITLES,
    )


def _resolve_titles(entries: list[RawEntry]) -> list[RawEntry]:
    """Full extraction per entry for hosts whose flat listing has no titles."""
    yt_dlp = _ytdlp()

    def one(entry: RawEntry) -> RawEntry:
        if not entry.unavailable:
            return entry
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as y:
                info = y.extract_info(entry.url, download=False) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("intake enumerate: %s unresolved: %s", entry.url, _short(exc))
            return entry
        resolved = _entry(info, entry.index or 0, fallback_url=entry.url)
        resolved.index = entry.index
        return resolved

    with ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS, thread_name_prefix="enum") as pool:
        return list(pool.map(one, entries))


def _single_youtube_fallback(url: str, exc: Exception) -> Listing:
    """A single video the bot-check refuses to extract: its oEmbed title still
    names the surah, and the acquire job (with cookies) does the fetch."""
    import json
    import urllib.parse
    import urllib.request

    if "list=" in url:
        raise EnumerationError(f"could not list {url}: {_short(exc)}") from exc
    oembed = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    try:
        with urllib.request.urlopen(oembed, timeout=20) as r:
            title = json.loads(r.read()).get("title") or ""
    except Exception as inner:  # noqa: BLE001
        raise EnumerationError(f"could not read {url}: {_short(exc)}") from inner
    return Listing(
        host="youtube", entries=[RawEntry(url=url, title=title, index=1)], source_url=url
    )


def _leaf(url: str) -> str:
    return (urlparse(url).path.rsplit("/", 1)[-1] or url)[:200]


def _short(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:300]
