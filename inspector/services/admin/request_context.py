"""Admin-facing context for a request row: where its audio comes from and how
far its reciter already is, so the Requests tab can filter and show it.

Built once per request-list rebuild (the list is cached on ``db_seq``):
``Context.build()`` reads the catalog snapshot and every state row once, then
``source_of`` / ``reciter_counts`` answer per row from memory, except
``chapter_urls`` for slug rows, which reads that delivery's (cached) manifest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from qua_shared.schemas import ReciterCatalog
from qua_shared.schemas.config.state import ReciterState
from services.admin.intake_plan.identity import channel_for
from services.audio import audio_meta
from services.db import repo_state
from services.state import catalog as catalog_service

log = logging.getLogger("inspector")

#: Channels whose files are fetched (yt-dlp / Drive), not streamed from a CDN.
PLAYLIST_CHANNELS = frozenset({"youtube", "drive", "soundcloud"})
SOURCE_PLAYLIST = "playlist"
SOURCE_CDN = "cdn"
#: States a reciter's delivery counts as "requested" in (anything past catalogued).
_REQUESTED_STATES = frozenset(s.value for s in ReciterState) - {ReciterState.CATALOGUED.value}


@dataclass
class Context:
    catalog: ReciterCatalog
    #: reciter_id → (released deliveries, requested-or-later deliveries, total)
    counts: dict[str, tuple[int, int, int]] = field(default_factory=dict)

    @classmethod
    def build(cls) -> Context:
        catalog = catalog_service.snapshot()
        states = {r.slug: r.state.value for r in repo_state.all_rows()}
        counts: dict[str, list[int]] = {}
        for d in catalog.deliveries:
            c = counts.setdefault(d.reciter_id, [0, 0, 0])
            state = states.get(d.slug)
            c[0] += state == ReciterState.RELEASED.value
            c[1] += state in _REQUESTED_STATES
            c[2] += 1
        return cls(catalog=catalog, counts={k: (a, b, t) for k, (a, b, t) in counts.items()})

    def channel_name(self, slug: str | None) -> str | None:
        if not slug:
            return None
        ch = next((c for c in self.catalog.vocab.channels if c.slug == slug), None)
        return ch.name if ch is not None else slug

    def reciter_counts(self, reciter_id: str | None) -> dict:
        released, requested, total = self.counts.get(reciter_id or "", (0, 0, 0))
        return {
            "reciter_published": released,
            "reciter_requested": requested,
            "reciter_deliveries": total,
        }

    def source_of(
        self, *, slug: str | None, channel: str | None, payload: dict, is_intake: bool
    ) -> dict:
        """``channel`` / ``channel_name`` / ``source_kind`` / ``source_url`` / ``link_count``."""
        if is_intake:
            src = payload.get("source") or {}
            links = src.get("links") or []
            url = src.get("playlist_url") or (links[0].get("url") if links else None)
            plan_channel = ((payload.get("plan") or {}).get("identity") or {}).get("channel")
            channel = plan_channel or channel_for(url, self.catalog) or None
            count = len(links) if src.get("method") != SOURCE_PLAYLIST else None
        else:
            urls = _chapter_urls(slug)
            url = urls.get("1") or next(iter(urls.values()), None)
            count = len(urls) or None
        if channel in PLAYLIST_CHANNELS or (
            is_intake and (payload.get("source") or {}).get("method") == SOURCE_PLAYLIST
        ):
            kind = SOURCE_PLAYLIST
        else:
            kind = SOURCE_CDN if (channel or url) else None
        return {
            "channel": channel,
            "channel_name": self.channel_name(channel),
            "source_kind": kind,
            "source_url": url,
            "link_count": count,
        }


def _chapter_urls(slug: str | None) -> dict[str, str]:
    if not slug:
        return {}
    try:
        return audio_meta.chapter_urls(slug)
    except Exception as exc:  # noqa: BLE001 — a missing manifest must not break the queue
        log.warning("admin requests: no audio manifest for %s: %s", slug, exc)
        return {}
