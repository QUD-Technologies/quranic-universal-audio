"""Propose + check the catalog identity an intake is minted under.

The catalog identity is proposed here from the request + the enumerated source and
then edited by the owner in the Requests tab:

* **channel** — the catalog channel whose ``host_patterns`` match the source URL
  (``youtube`` / ``drive`` / ``soundcloud`` / ``archive_org`` / a CDN).
* **source** — the provenance: the generic source for its host (created on mint when absent)
  (``google_drive`` / ``soundcloud`` / ``archive_org``), else a new
  ``<uploader>_youtube`` source built from the playlist's channel.
* **slug** — ``<reciter_id>[_<riwayah>][_<style>][_<year>]_<channel_short>``
  (catalog.md §3), with a ``_v2``… disambiguator on collision.
"""

from __future__ import annotations

import fnmatch
import re
import unicodedata
from urllib.parse import urlparse

from qua_shared.schemas import PlanIdentity, ReciterCatalog
from qua_shared.schemas.bucket.catalog import SOURCE_SLUG_RE
from qua_shared.schemas.config.state import SLUG_RE

DEFAULT_RIWAYAH = "hafs_an_asim"
DEFAULT_STYLE = "murattal"
#: Existing generic provenance per host; YouTube sources are per uploader.
_HOST_SOURCES = {
    "drive": ("google_drive", "Google Drive", "https://drive.google.com"),
    "soundcloud": ("soundcloud", "SoundCloud", "https://soundcloud.com"),
    "archive": ("archive_org", "Internet Archive", "https://archive.org"),
}
_MAX_DISAMBIGUATOR = 20


def slugify(text: str | None) -> str:
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return re.sub(r"_+", "_", s)[:60].strip("_")


def channel_for(url: str | None, catalog: ReciterCatalog) -> str:
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    if not host:
        return ""
    for channel in catalog.vocab.channels:
        if any(_host_matches(host, p.lower()) for p in channel.host_patterns):
            return channel.slug
    return ""


def _host_matches(host: str, pattern: str) -> bool:
    """``*.archive.org`` also covers the bare ``archive.org``."""
    bare = pattern.removeprefix("*.")
    return fnmatch.fnmatch(host, pattern) or host == bare or host == f"www.{bare}"


def propose(
    *,
    kind: str,
    payload: dict,
    host: str,
    source_url: str | None,
    uploader: str | None,
    uploader_url: str | None,
    catalog: ReciterCatalog,
) -> PlanIdentity:
    edits = payload.get("proposed_edits") or {}
    reciter_id = (payload.get("reciter_id") or "").strip()
    name_en, name_ar, country = edits.get("name_en"), edits.get("name_ar"), edits.get("country")
    if kind == "new_reciter":
        reciter_id = slugify(name_en)
    else:
        existing = next((r for r in catalog.reciters if r.reciter_id == reciter_id), None)
        if existing is not None:
            name_en, name_ar, country = existing.name_en, existing.name_ar, existing.country
    identity = PlanIdentity(
        reciter_id=reciter_id,
        name_en=name_en,
        name_ar=name_ar,
        country=country,
        channel=channel_for(source_url, catalog),
        recording_year=edits.get("recording_year"),
    )
    _propose_source(identity, host, uploader, uploader_url, source_url, catalog)
    identity.slug = propose_slug(identity, edits, catalog)
    return identity


def _propose_source(identity, host, uploader, uploader_url, source_url, catalog) -> None:
    known = {s.slug for s in catalog.vocab.sources}
    generic = _HOST_SOURCES.get(host)
    if generic is not None:
        identity.source = generic[0]
        if generic[0] not in known:
            identity.new_source_name, identity.new_source_url = generic[1], generic[2]
        return
    if host == "youtube" and uploader:
        slug = f"{slugify(uploader) or 'uploader'}_youtube"
        identity.source = slug
        if slug not in known:
            identity.new_source_name = f"{uploader} (YouTube)"
            identity.new_source_url = uploader_url
        return
    netloc = (urlparse(source_url or "").netloc or "").lower()
    for src in catalog.vocab.sources:
        home = (urlparse(src.url or "").netloc or "").lower().removeprefix("www.")
        if home and netloc.endswith(home):
            identity.source = src.slug
            return


def propose_slug(identity: PlanIdentity, edits: dict, catalog: ReciterCatalog) -> str:
    vocab = catalog.vocab
    parts = [identity.reciter_id]
    riwayah = edits.get("riwayah") or DEFAULT_RIWAYAH
    style = edits.get("style") or DEFAULT_STYLE
    if riwayah != DEFAULT_RIWAYAH:
        parts.append(next((r.short for r in vocab.riwayat if r.slug == riwayah), riwayah))
    if style != DEFAULT_STYLE:
        parts.append(next((s.short for s in vocab.styles if s.slug == style), style))
    if identity.recording_year:
        parts.append(str(identity.recording_year))
    short = next((c.short for c in vocab.channels if c.slug == identity.channel), identity.channel)
    parts.append(short or "src")
    base = "_".join(p for p in parts if p)
    taken = {d.slug for d in catalog.deliveries}
    if base not in taken:
        return base
    for n in range(2, _MAX_DISAMBIGUATOR):
        if f"{base}_v{n}" not in taken:
            return f"{base}_v{n}"
    return base


def check(identity: PlanIdentity, *, kind: str, catalog: ReciterCatalog) -> list[str]:
    """Blocking problems with the identity as the owner left it."""
    errors: list[str] = []
    if not SLUG_RE.match(identity.slug or ""):
        errors.append("Delivery slug must be lowercase letters, digits and underscores.")
    elif any(d.slug == identity.slug for d in catalog.deliveries):
        errors.append(f"Delivery slug {identity.slug} already exists.")
    if not SLUG_RE.match(identity.reciter_id or ""):
        errors.append("Reciter id must be lowercase letters, digits and underscores.")
    known_reciter = any(r.reciter_id == identity.reciter_id for r in catalog.reciters)
    if kind == "existing_reciter_new_combo" and not known_reciter:
        errors.append(f"Reciter {identity.reciter_id} is not in the catalog.")
    if kind == "new_reciter" and not known_reciter and not (identity.name_en or "").strip():
        errors.append("A new reciter needs an English name.")
    if not any(c.slug == identity.channel for c in catalog.vocab.channels):
        errors.append("Pick the channel the audio comes from.")
    if not SOURCE_SLUG_RE.match(identity.source or ""):
        errors.append("Pick an existing source or name a new one.")
    elif not any(s.slug == identity.source for s in catalog.vocab.sources):
        if not (identity.new_source_name or "").strip():
            errors.append(f"New source {identity.source} needs a display name.")
    return errors
