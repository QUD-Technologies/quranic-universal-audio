"""Google Drive folder listing without an OAuth app.

The public folder page and ``embeddedfolderview`` both stop at 50 files, and
yt-dlp's folder extractor is broken, so the listing goes through the Drive v3
``files.list`` API with the browser API key the folder page itself embeds (the
same request the Drive web client makes). Several keys are embedded; only some
are allowed to list, so each is tried in turn. ``INSPECTOR_GOOGLE_API_KEY``, when
set, is tried first — the fallback if Google ever stops embedding a usable key.

Sub-folders are walked one level down (a "Juz 30" folder inside the mushaf
folder); non-audio files (cover art, PDFs) are skipped.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("inspector")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
_TIMEOUT_S = 30
_PAGE_SIZE = 1000
_MAX_DEPTH = 1
_FOLDER_MIME = "application/vnd.google-apps.folder"
_AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4", ".webm")
_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_FOLDER_ID_RE = re.compile(r"/folders/([A-Za-z0-9_\-]{10,})")
_FILE_ID_RE = re.compile(r"/file/d/([A-Za-z0-9_\-]{10,})")
_QUERY_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_\-]{10,})")


class DriveError(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str

    @property
    def url(self) -> str:
        return file_url(self.id)


def file_url(file_id: str) -> str:
    """The canonical per-file URL (the form every existing Drive delivery uses)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def folder_id(url: str) -> str | None:
    m = _FOLDER_ID_RE.search(url)
    if m:
        return m.group(1)
    if "/folderview" in url or "embeddedfolderview" in url:
        q = _QUERY_ID_RE.search(url)
        return q.group(1) if q else None
    return None


def file_id(url: str) -> str | None:
    m = _FILE_ID_RE.search(url)
    if m:
        return m.group(1)
    if "/folders/" in url:
        return None
    q = _QUERY_ID_RE.search(url)
    return q.group(1) if q else None


def list_folder(fid: str) -> list[DriveFile]:
    """Every audio file in the folder (and its direct sub-folders), name-sorted."""
    keys = _candidate_keys(fid)
    last: Exception | None = None
    for key in keys:
        try:
            files = _walk(fid, key, depth=0, prefix="")
        except DriveError as exc:
            last = exc
            continue
        return sorted(files, key=lambda f: _natural_key(f.name))
    raise DriveError(
        f"could not list Drive folder {fid}: "
        + (str(last) if last else "no usable API key on the folder page")
        + " — is the folder shared as 'Anyone with the link'?"
    )


def _candidate_keys(fid: str) -> list[str]:
    keys: list[str] = []
    env_key = (os.environ.get("INSPECTOR_GOOGLE_API_KEY") or "").strip()
    if env_key:
        keys.append(env_key)
    try:
        html = _get(f"https://drive.google.com/drive/folders/{fid}").decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — a missing page just leaves the env key
        log.warning("drive: folder page %s unreadable: %s", fid, exc)
        return keys
    for key in sorted(set(_KEY_RE.findall(html))):
        if key not in keys:
            keys.append(key)
    return keys


def _walk(fid: str, key: str, *, depth: int, prefix: str) -> list[DriveFile]:
    out: list[DriveFile] = []
    for item in _list_children(fid, key):
        name = item.get("name") or ""
        if item.get("mimeType") == _FOLDER_MIME:
            if depth < _MAX_DEPTH:
                out.extend(_walk(item["id"], key, depth=depth + 1, prefix=f"{prefix}{name}/"))
            continue
        if _is_audio(item):
            out.append(DriveFile(id=item["id"], name=f"{prefix}{name}"))
    return out


def _list_children(fid: str, key: str) -> list[dict]:
    items: list[dict] = []
    token: str | None = None
    while True:
        params = {
            "q": f"'{fid}' in parents and trashed = false",
            "key": key,
            "pageSize": str(_PAGE_SIZE),
            "fields": "nextPageToken,files(id,name,mimeType)",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
        try:
            doc = json.loads(_get(url, referer="https://drive.google.com/"))
        except urllib.error.HTTPError as exc:
            raise DriveError(f"files.list HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise DriveError(f"files.list failed: {exc}") from exc
        items.extend(doc.get("files") or [])
        token = doc.get("nextPageToken")
        if not token:
            return items


def _is_audio(item: dict) -> bool:
    mime = item.get("mimeType") or ""
    name = (item.get("name") or "").lower()
    return mime.startswith(("audio/", "video/")) or name.endswith(_AUDIO_EXTS)


def _get(url: str, *, referer: str | None = None) -> bytes:
    headers = {"User-Agent": _UA}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=_TIMEOUT_S
    ) as r:
        return r.read()


def _natural_key(name: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]
