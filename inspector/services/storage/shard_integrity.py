"""Shard-integrity sweep — find chapters whose timestamps shard vanished.

``qua_shared.timestamps_shards.write_validated_shard`` writes a hidden temp
(``.{chapter}.json.br.{rand}``), fsyncs it, then renames it over the real file.
That rename is NOT atomic on the bucket: when the writer is **killed** (OOM /
SIGKILL, so its own ``except BaseException`` cleanup never runs) the destination
can already be gone, leaving only the orphan. The chapter then disappears from
Timestamps while audio / peaks / detailed.json stay intact.

Nothing else catches this. ``coverage_report.json`` still reads ``clean``, the
Inspector still lists the chapter (its audio and segments are there), and the GH
cut only notices as a "missing coverage" line on the reciters a given cut
happens to include — long after the fact. Hence this sweep.

Flask-free and storage-only: it takes the slugs to check and returns findings.
Deciding what to do with them (notify, log, print) belongs to the caller.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from services.storage.hf_bucket import get_backend
from services.storage.storage_paths import reciter_file

logger = logging.getLogger("inspector")

#: ``<chapter>.json.br`` — a healthy shard.
_SHARD_RE = re.compile(r"^(\d+)\.json\.br$")
#: ``.<chapter>.json.br.<rand>`` — an orphaned atomic-write temp.
_ORPHAN_RE = re.compile(r"^\.(\d+)\.json\.br\..+$")
#: ``<chapter>.mp3`` — prefetched chapter audio.
_AUDIO_RE = re.compile(r"^(\d+)\.mp3$")

FindingKind = Literal["orphan_temp", "missing_shard"]


@dataclass(frozen=True)
class IntegrityFinding:
    """One chapter of one delivery whose shard is missing.

    ``orphan_temp`` is the recoverable case — the orphan holds the complete
    payload, so the repair is to re-validate its bytes and publish them to
    ``path``. ``missing_shard`` has no orphan left, so the chapter needs a
    single-chapter re-align.
    """

    slug: str
    chapter: int
    kind: FindingKind
    #: The orphan's bucket path (``orphan_temp`` only) — where the payload is.
    orphan_path: str | None = None

    @property
    def source_key(self) -> str:
        """Stable per-(slug, chapter, kind) notification key: a gap that stays
        unrepaired must not re-notify on every sweep."""
        return f"shardint:{self.kind}:{self.slug}:{self.chapter}"


def _list_strict(path: str) -> list[str]:
    """List ``path``, letting a backend/API failure raise.

    Deliberately NOT ``list_dir``: that turns an API error into an empty list,
    which here would read as "every shard is gone" and fire a storm of false
    alarms on a transient bucket hiccup.
    """
    backend = get_backend()
    return backend.list_dir_strict(path)


def scan_delivery(slug: str) -> list[IntegrityFinding]:
    """Findings for one delivery. Raises if its directories can't be listed —
    the caller decides whether that's fatal or a skip."""
    shard_names = _list_strict(reciter_file(slug, "timestamps"))
    shards = {int(m.group(1)) for n in shard_names if (m := _SHARD_RE.match(n))}
    orphans = {int(m.group(1)): n for n in shard_names if (m := _ORPHAN_RE.match(n))}

    # A delivery with no shards at all was simply never timestamped (it is also
    # absent from every release). Only a partially-timestamped one can have lost
    # a shard, so the whole-reciter case is not a finding.
    if not shards and not orphans:
        return []

    audio = {
        int(m.group(1))
        for n in _list_strict(reciter_file(slug, "audio"))
        if (m := _AUDIO_RE.match(n))
    }

    findings: list[IntegrityFinding] = []
    for chapter in sorted(set(orphans) | (audio - shards)):
        if chapter in shards:
            # A stale orphan beside a healthy shard: a *previous* write that
            # failed and was later redone. Nothing is missing, so it is litter,
            # not a finding.
            continue
        orphan = orphans.get(chapter)
        findings.append(
            IntegrityFinding(
                slug=slug,
                chapter=chapter,
                kind="orphan_temp" if orphan else "missing_shard",
                orphan_path=reciter_file(slug, f"timestamps/{orphan}") if orphan else None,
            )
        )
    return findings


def scan(slugs: list[str]) -> tuple[list[IntegrityFinding], list[str]]:
    """Sweep every slug. Returns ``(findings, unreadable_slugs)``.

    A slug whose listing fails is reported separately rather than as findings —
    "we could not look" must never be rendered as "the data is gone".
    """
    findings: list[IntegrityFinding] = []
    unreadable: list[str] = []
    for slug in slugs:
        try:
            findings.extend(scan_delivery(slug))
        except Exception:  # noqa: BLE001 — one unreadable delivery never aborts the sweep
            logger.exception("shard_integrity: could not scan %s", slug)
            unreadable.append(slug)
    return findings, unreadable
