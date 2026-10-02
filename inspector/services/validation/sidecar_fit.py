"""Does a uid-keyed boundary sidecar entry still describe its segment?

The boundary sidecars (``missed_waqf``, ``hidden_pause``, ``auto_split``) are
computed offline and keyed by ``segment_uid``. An edit that keeps the uid — a
reference edit, a trim — leaves the entry describing a segment that no longer
exists: a 34:1 row whose ref was corrected from Al-Fatiha still carried a
Low Confidence Waqf card cut at Fatiha's verse ends.

An entry's ``refs`` are the pieces of the segment's ``matched_ref`` at build
time, so a live entry's first piece starts where the segment starts and its last
piece ends where it ends, and every cursor falls inside the segment's audio.
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


def fitting_entries(by_uid: dict[str, dict], entries: list[dict]) -> dict[str, dict]:
    """``by_uid`` without the entries whose segment changed under them.

    Entries whose uid no longer exists are dropped too: nothing can render them.
    """
    if not by_uid:
        return by_uid
    out: dict[str, dict] = {}
    for entry in entries:
        for seg in entry.get("segments", []):
            uid = seg.get("segment_uid")
            if uid and uid in by_uid and entry_fits_segment(by_uid[uid], seg):
                out[uid] = by_uid[uid]
    return out
