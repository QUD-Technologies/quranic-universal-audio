"""Does a uid-keyed boundary sidecar entry still describe its segment?

The boundary sidecars (``missed_waqf``, ``hidden_pause``, ``auto_split``) are
computed offline and keyed by ``segment_uid``. An edit that keeps the uid — a
reference edit, a trim — leaves the entry describing a segment that no longer
exists: a 34:1 row whose ref was corrected from Al-Fatiha still carried a
Low Confidence Waqf card cut at Fatiha's verse ends.

An entry's ``refs`` are the pieces of the segment's ``matched_ref`` at build
time, so a live entry's first piece starts where the segment starts and its last
piece ends where it ends, and every cursor falls inside the segment's audio.
A segment split from its card keeps its uid, so it is judged together with its
split pieces: the span they cover, first piece's start to last piece's end.
"""

from __future__ import annotations


def entry_fits_segment(hit: object, seg: dict) -> bool:
    """True when ``hit`` still cuts ``seg`` as it is now.

    An entry without ``refs`` (a wraparound segment, whose pieces have no single
    span) is judged on its cursors alone.
    """
    if not isinstance(hit, dict):
        return False
    ref = str(seg.get("matched_ref") or "")
    refs = hit.get("refs") or []
    if refs and (
        refs[0].partition("-")[0] != ref.partition("-")[0]
        or refs[-1].rpartition("-")[2] != ref.rpartition("-")[2]
    ):
        return False
    return all(seg["time_start"] < c < seg["time_end"] for c in hit.get("cursors") or [])


def _group_span(segs: list[dict]) -> dict:
    """One segment covering ``segs`` (a root and its split pieces), in time order."""
    ordered = sorted(segs, key=lambda seg: seg["time_start"])
    first = str(ordered[0].get("matched_ref") or "").partition("-")[0]
    last = str(ordered[-1].get("matched_ref") or "").rpartition("-")[2]
    return {
        "matched_ref": f"{first}-{last}",
        "time_start": ordered[0]["time_start"],
        "time_end": max(seg["time_end"] for seg in segs),
    }


def fitting_entries(
    by_uid: dict[str, dict],
    entries: list[dict],
    groups: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """``by_uid`` without the entries whose segment changed under them.

    ``groups`` maps a split root's uid to its pieces' uids; a root is judged over
    the span of the pieces still live. Entries whose uid no longer exists are
    dropped too: nothing can render them.
    """
    if not by_uid:
        return by_uid
    live = {
        seg.get("segment_uid"): (i, seg)
        for i, entry in enumerate(entries)
        for seg in entry.get("segments", [])
        if seg.get("segment_uid")
    }
    out: dict[str, dict] = {}
    for uid, hit in by_uid.items():
        if uid not in live:
            continue
        where, seg = live[uid]
        pieces = [
            live[u][1]
            for u in (groups or {}).get(uid, [])
            if u in live and live[u][0] == where
        ]
        if entry_fits_segment(hit, _group_span([seg, *pieces]) if pieces else seg):
            out[uid] = hit
    return out
