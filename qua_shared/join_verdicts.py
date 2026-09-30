"""Word ownership of reviewed joins, shared by save adapters.

An answer belongs to its word (``after_ref``) and stays with whichever piece holds
that word: a WAQF at the piece's last word, re-anchored to its end, or a WASL on an
inner word, kept inside the piece's audio. Anything else is dropped.
"""


def contained_verdicts(segment: dict, answers: list[dict]) -> list[dict]:
    def key(ref):
        return tuple(map(int, ref.split(":")))

    try:
        parts = segment["matched_ref"].split("-")
        start, end = key(parts[0]), key(parts[-1])
        lo, hi = segment["time_start"], segment["time_end"]
        out = []
        for j in answers:
            word = key(j["after_ref"])
            if not start <= word <= end:
                continue
            if word == end and j["verdict"] == "waqf":
                out.append({**j, "at_ms": hi})
            elif word < end and j["verdict"] == "wasl" and hi - lo > 1:
                out.append({**j, "at_ms": min(max(j["at_ms"], lo + 1), hi - 1)})
        return out
    except (ValueError, KeyError, TypeError):
        return []
