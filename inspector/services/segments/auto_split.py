"""Auto-split: look up precomputed cursor positions for a segment.

The actual MFA alignment that produces the cursor positions happens
**offline**, via ``qua_shared/auto_split_precompute.py``, after segment
extraction. The result is persisted to ``<reciter>/auto_split_v1.json``
keyed by ``segment_uid``. At runtime the Inspector reads that sidecar via
``services.data_loader.load_auto_split`` and serves it in ~10 ms instead of
making a 5–15 s round trip to the MFA Space.

Returns the same shape the frontend has been consuming since the N-cursor
extension::

    {"cursors": list[int] | None,        # absolute ms cuts (N-1 entries)
     "refs":    list[str] | None,        # N per-section refs
     "kind":    "cross_verse" | "repetition" | "missed_waqf" | "hidden_pause" | None,
     "source":  "sidecar" | "miss"}

``missed_waqf_v1.json`` and ``hidden_pause_v1.json`` entries that carry
per-section ``refs`` are merged into the map with ``kind="missed_waqf"`` /
``kind="hidden_pause"``. Precedence per uid: ``auto_split_v1``, then
``missed_waqf``, then ``hidden_pause``; entries without refs are omitted so
the row falls back to plain Split.

A cross-verse piece an edit made (or reshaped) inherits its ancestor's cuts: each of its verse
ends takes the cursor the sidecar holds for that word, when every one lies inside
the piece. Otherwise (offline alignment failed, or no cut inside) the
response is the ``"miss"`` envelope with all-null payload. The frontend
flips the row's button label from *Auto Split* back to plain *Split* and
falls back to manual single-cursor placement — same UX as a non-candidate
seg has always had.
"""

from __future__ import annotations

import logging

from services.storage.data_loader import (
    load_auto_split,
    load_detailed,
    load_hidden_pause,
    load_missed_waqf,
)
from utils.references import chapter_from_ref

logger = logging.getLogger(__name__)


def _find_segment_kind(reciter: str, chapter: int, segment_uid: str) -> str | None:
    """Return ``"cross_verse"`` / ``"repetition"`` / ``None``.

    Used only by the response's ``kind`` field when the sidecar reports a
    miss — so we can tell the frontend whether the row was an auto-split
    candidate at all. (Sidecar hits already carry ``kind`` from the offline
    pre-compute, so this lookup is the cold path.)
    """
    for entry in load_detailed(reciter):
        if chapter_from_ref(entry.get("ref", "")) != chapter:
            continue
        for seg in entry.get("segments", []):
            if seg.get("segment_uid") != segment_uid:
                continue
            wrap = seg.get("wrap_word_ranges") or None
            if wrap:
                return "repetition"
            mref = seg.get("matched_ref", "") or ""
            parts = mref.split("-")
            if len(parts) == 2:
                s, e = parts[0].split(":"), parts[1].split(":")
                if len(s) >= 2 and len(e) >= 2 and s[1] != e[1]:
                    return "cross_verse"
            return None
    return None


def _cursor_by_word(by_uid: dict[str, dict]) -> dict[str, int]:
    """Every cross-verse cut in ``by_uid`` as ``{verse-end word: cursor ms}``."""
    out: dict[str, int] = {}
    for hit in by_uid.values():
        if not isinstance(hit, dict) or hit.get("kind") != "cross_verse":
            continue
        for ref, cursor in zip(hit.get("refs") or [], hit.get("cursors") or [], strict=False):
            out[str(ref).rpartition("-")[2]] = cursor
    return out


def _inherited(seg: dict, cursors: dict[str, int], word_counts) -> dict | None:
    """A cross-verse entry for a piece an edit made: its ancestor's cuts at its verse ends."""
    from services.validation.detail import _verse_end_refs

    ref = seg.get("matched_ref") or ""
    ends = _verse_end_refs(ref, word_counts)
    at = [cursors.get(end) for end in ends]
    if not ends or any(c is None or not seg["time_start"] < c < seg["time_end"] for c in at):
        return None
    starts = [ref.partition("-")[0]] + [
        f"{e.split(':')[0]}:{int(e.split(':')[1]) + 1}:1" for e in ends
    ]
    stops = [*ends, ref.rpartition("-")[2]]
    return {
        "cursors": at,
        "refs": [f"{a}-{b}" for a, b in zip(starts, stops, strict=True)],
        "kind": "cross_verse",
    }


def _with_inherited(reciter: str, by_uid: dict[str, dict]) -> dict[str, dict]:
    """``by_uid`` with an inherited entry for every live cross-verse seg whose own entry
    is missing or no longer fits it."""
    from services.reference.delivery_edition import sdk_riwayah_for
    from services.storage.data_loader import get_word_counts

    cursors = _cursor_by_word(by_uid)
    entries = load_detailed(reciter) if cursors else []
    if not entries:
        return by_uid
    word_counts = get_word_counts(sdk_riwayah_for(reciter))
    out = dict(by_uid)
    for entry in entries:
        for seg in entry.get("segments", []):
            uid = seg.get("segment_uid")
            if (
                uid
                and not _fits(out.get(uid), seg)
                and (hit := _inherited(seg, cursors, word_counts))
            ):
                out[uid] = hit
    return out


def _fits(hit: dict | None, seg: dict) -> bool:
    """True when ``hit`` still cuts ``seg`` as it is (a piece keeps its parent's uid)."""
    if not isinstance(hit, dict):
        return False
    if hit.get("kind") != "cross_verse":
        return True
    refs, at = hit.get("refs") or [], hit.get("cursors") or []
    return (
        bool(refs)
        and refs[0].partition("-")[0] == str(seg.get("matched_ref")).partition("-")[0]
        and refs[-1].rpartition("-")[2] == str(seg.get("matched_ref")).rpartition("-")[2]
        and all(seg["time_start"] < c < seg["time_end"] for c in at)
    )


def _merged_by_uid(reciter: str) -> dict[str, dict]:
    """``auto_split_v1`` entries (plus the entries pieces inherit from them) and the
    ``missed_waqf_v1`` and ``hidden_pause_v1`` entries that have refs, first source
    winning per uid."""
    by_uid, _meta = load_auto_split(reciter)
    merged = _with_inherited(reciter, dict(by_uid))
    for kind, loader in (("missed_waqf", load_missed_waqf), ("hidden_pause", load_hidden_pause)):
        sidecar, _smeta = loader(reciter)
        for uid, hit in sidecar.items():
            if uid in merged or not isinstance(hit, dict) or not hit.get("refs"):
                continue
            merged[uid] = {"cursors": hit.get("cursors"), "refs": hit["refs"], "kind": kind}
    return merged


def compute_auto_split(reciter: str, chapter: int, segment_uid: str) -> dict:
    """Resolve the precomputed Auto Split cursors for a single segment.

    Returns the response envelope described in the module docstring. Reads
    are O(1) against the per-reciter in-memory ``load_auto_split`` cache.
    No MFA, no audio fetch, no ffmpeg — those moved offline.
    """
    hit = _merged_by_uid(reciter).get(segment_uid)
    if hit is not None:
        cursors = hit.get("cursors") or None
        refs = hit.get("refs") or None
        kind = hit.get("kind") or None
        # Sanity: a sidecar entry without cursors / refs is meaningless;
        # treat it as a miss rather than ship a half-shape to the FE.
        if cursors and refs and kind:
            return {"cursors": list(cursors), "refs": list(refs), "kind": kind, "source": "sidecar"}

    # Sidecar miss: tell the FE this row has no precomputed cursors. The
    # button falls back to a plain Split. ``kind`` is still useful so the
    # FE can colour-code or telemetry-tag the miss.
    return {
        "cursors": None,
        "refs": None,
        "kind": _find_segment_kind(reciter, chapter, segment_uid),
        "source": "miss",
    }


def load_auto_split_map(reciter: str) -> dict[str, dict]:
    """Return the whole precomputed Auto Split map for *reciter*, filtered.

    The bulk sibling of :func:`compute_auto_split`: instead of one ``uid`` per
    request it returns ``{segment_uid: {"cursors": [...], "refs": [...],
    "kind": ...}}`` for *every* sidecar hit. The FE preloads this once (on
    accordion open) so each Auto Split click is a zero-network O(1) map lookup
    instead of a round trip, and can classify misses ("uid not in map")
    client-side without the O(n) ``_find_segment_kind`` scan.

    Applies the same validity gate ``compute_auto_split`` uses per-uid — an
    entry missing any of ``cursors`` / ``refs`` / ``kind`` is dropped rather
    than shipped half-shaped. Reads the O(1) in-memory ``load_auto_split``
    cache (one bucket read per reciter per process).
    """
    out: dict[str, dict] = {}
    for uid, hit in _merged_by_uid(reciter).items():
        if not isinstance(hit, dict):
            continue
        cursors = hit.get("cursors") or None
        refs = hit.get("refs") or None
        kind = hit.get("kind") or None
        if cursors and refs and kind:
            out[uid] = {"cursors": list(cursors), "refs": list(refs), "kind": kind}
    return out
