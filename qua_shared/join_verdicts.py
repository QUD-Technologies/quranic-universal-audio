"""Word and audio ownership of reviewed joins, shared by save adapters."""


def contained_verdicts(segment: dict, answers: list[dict]) -> list[dict]:
    def key(ref):
        return tuple(map(int, ref.split(":")))

    try:
        parts = segment["matched_ref"].split("-")
        start, end = key(parts[0]), key(parts[-1])
        return [
            j
            for j in answers
            if segment["time_start"] < j["at_ms"] <= segment["time_end"]
            and start <= key(j["after_ref"]) <= end
            and (
                (
                    j["verdict"] == "waqf"
                    and j["at_ms"] == segment["time_end"]
                    and key(j["after_ref"]) == end
                )
                or (
                    j["verdict"] == "wasl"
                    and j["at_ms"] < segment["time_end"]
                    and key(j["after_ref"]) < end
                )
            )
        ]
    except (ValueError, KeyError):
        return []
