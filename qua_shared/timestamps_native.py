"""Timing projections from timestamp shard documents.

Two projections over the same segments:

- **canonical** (``project_shard``): one occasion per verse — the earliest
  complete continuous take — keyed by ref. What the HF dataset, coverage and
  the FE read.
- **all occurrences** (``project_shard_occurrences``): every contiguous recited
  span as its own row in audio order, the canonical one flagged. What the GH
  release timeline emits, so a repeated verse, a leading false-start or a
  trailing partial is a row of its own rather than lost.

Both profiles land in the same shape — words with source-relative times. The
difference is depth: a native shard projects letters as well, a word-profile
shard has none to project and emits ``letters: []`` on every word.
"""

from __future__ import annotations

from collections import defaultdict

from qua_shared.timestamps_codec import decode_document
from qua_shared.timestamps_shards import NATIVE_SHARD_SCHEMA_VERSIONS, shard_profile


def _index(ref: str) -> int:
    return int(ref.rsplit(":", 1)[1])


def _reading_segments(reading: dict) -> list[dict]:
    result = reading["analysis"]["result"]
    words = {int(row["id"]): row for row in result["words"]}
    word_times = {int(row["word_id"]): row for row in reading["timing"]["words"]}
    tokens_by_word: dict[int, list[dict]] = defaultdict(list)
    for token in reading["timing"]["animation_tokens"]:
        tokens_by_word[int(token["word_id"])].append(token)

    out = []
    for part in reading["parts"]:
        timed_words = []
        for word_id in part["word_ids"]:
            word = words[int(word_id)]
            timing = word_times[int(word_id)]
            word_tokens = tokens_by_word[int(word_id)]
            source_offset = min(
                int(value) for token in word_tokens for value in token["character_ids"]
            )
            letters = []
            for token in word_tokens:
                letters.append(
                    {
                        "source_unit_ids": list(map(int, token["source_unit_ids"])),
                        "text": token["text"],
                        "character_offsets": [
                            int(value) - source_offset for value in token["character_ids"]
                        ],
                        "paint_character_offsets": [
                            int(value) - source_offset for value in token["paint_character_ids"]
                        ],
                        "sound_ids": list(map(int, token["sound_ids"])),
                        "start_ms": token["start_ms"],
                        "end_ms": token["end_ms"],
                        "policy": token["policy"],
                    }
                )
            timed_words.append(
                {
                    "word_id": int(word_id),
                    "index": _index(word["ref"]),
                    "ref": word["ref"],
                    "source_text": word["text"],
                    "start_ms": int(timing["start_ms"]),
                    "end_ms": int(timing["end_ms"]),
                    "letters": letters,
                }
            )
        out.append({"ref": part["ref"], "t": list(part["t"]), "words": timed_words})
    return out


def _split_occasions(segments: list[dict], foreign_starts: list[int]) -> list[list[dict]]:
    occasions: list[list[dict]] = []
    current: list[dict] = []
    for segment in segments:
        if current and any(current[-1]["t"][0] < at < segment["t"][0] for at in foreign_starts):
            occasions.append(current)
            current = []
        current.append(segment)
    if current:
        occasions.append(current)
    return occasions


def _coverage(segments: list[dict]) -> set[int]:
    return {word["index"] for segment in segments for word in segment["words"]}


def _completion(segments: list[dict], target: set[int]) -> int | None:
    covered: set[int] = set()
    for index, segment in enumerate(segments):
        covered |= _coverage([segment])
        if target <= covered:
            return index
    return None


def _canonical(occasions: list[list[dict]], target: set[int]) -> list[dict]:
    completing = [one for one in occasions if _completion(one, target) is not None]
    chosen = completing[0] if completing else max(occasions, key=lambda one: len(_coverage(one)))
    end = _completion(chosen, target)
    kept = chosen if end is None else chosen[: end + 1]
    restart = 0
    for index in range(1, len(kept)):
        words = kept[index]["words"]
        if words and words[0]["index"] == 1 and target <= _coverage(kept[index:]):
            restart = index
    return kept[restart:]


def _project(segments: list[dict]) -> dict:
    words = [word for segment in segments for word in segment["words"]]
    starts = [int(segment["t"][0]) for segment in segments]
    ends = [int(segment["t"][1]) for segment in segments]
    spans = []
    cursor = 0
    for segment in segments:
        count = len(segment["words"])
        if count:
            spans.append(
                {
                    "ref": segment["ref"],
                    "w_from": segment["words"][0]["index"],
                    "w_to": segment["words"][-1]["index"],
                    "occ_start": cursor,
                    "occ_end": cursor + count,
                    "start_ms": segment["words"][0]["start_ms"],
                    "end_ms": segment["words"][-1]["end_ms"],
                }
            )
        cursor += count
    for word in words:
        starts.append(word["start_ms"])
        ends.append(word["end_ms"])
        starts.extend(row["start_ms"] for row in word["letters"] if row["start_ms"] is not None)
        ends.extend(row["end_ms"] for row in word["letters"] if row["end_ms"] is not None)
    return {
        "words": words,
        "verse_start_ms": min(starts),
        "verse_end_ms": max(ends),
        "segments": spans,
    }


def _group_by_ref(segments: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[int]]]:
    segments.sort(key=lambda row: row["t"][0])
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for segment in segments:
        by_ref[segment["ref"]].append(segment)
    starts = {ref: [row["t"][0] for row in rows] for ref, rows in by_ref.items()}
    return by_ref, starts


def _foreign_starts(ref: str, starts: dict[str, list[int]]) -> list[int]:
    return sorted(at for other, values in starts.items() if other != ref for at in values)


def _target(rows: list[dict]) -> set[int]:
    return set(range(1, max(_coverage(rows), default=0) + 1))


def _select_occasions(segments: list[dict]) -> dict[str, dict]:
    """One canonical occasion per verse, from every segment in audio order."""
    by_ref, starts = _group_by_ref(segments)
    out = {}
    for ref, rows in by_ref.items():
        occasions = _split_occasions(rows, _foreign_starts(ref, starts))
        out[ref] = _project(_canonical(occasions, _target(rows)))
    return out


def _occurrences(occasions: list[list[dict]], target: set[int]) -> list[tuple[bool, list[dict]]]:
    """Every contiguous recited span of one verse, the canonical one flagged.

    ``_canonical`` returns a contiguous sub-slice of exactly one occasion (a
    leading false-start and trailing post-completion segments trimmed off).
    Those trimmed ends become their own unflagged spans — they are audio the
    reciter produced, just not part of the clean take — and every other
    occasion is emitted whole and unflagged. Within-take lookbacks stay
    embedded in their span.
    """
    canonical = _canonical(occasions, target)
    out: list[tuple[bool, list[dict]]] = []
    for occasion in occasions:
        first = next((i for i, seg in enumerate(occasion) if seg is canonical[0]), None)
        if first is None:
            out.append((False, occasion))
            continue
        lead, trail = occasion[:first], occasion[first + len(canonical) :]
        if lead:
            out.append((False, lead))
        out.append((True, canonical))
        if trail:
            out.append((False, trail))
    return out


def _all_occasions(segments: list[dict]) -> list[dict]:
    """Every occurrence of every verse, in audio order, canonical flagged."""
    by_ref, starts = _group_by_ref(segments)
    out: list[dict] = []
    for ref, rows in by_ref.items():
        occasions = _split_occasions(rows, _foreign_starts(ref, starts))
        for canonical, span in _occurrences(occasions, _target(rows)):
            if not any(segment["words"] for segment in span):
                continue
            out.append({"ref": ref, "canonical": canonical, **_project(span)})
    out.sort(key=lambda row: (row["verse_start_ms"], not row["canonical"], row["ref"]))
    return out


def _native_segments(shard: dict) -> list[dict]:
    version = (shard.get("_meta") or {}).get("schema_version")
    if version not in NATIVE_SHARD_SCHEMA_VERSIONS:
        raise ValueError(
            f"native timestamp shard must use schema version "
            f"{' or '.join(map(str, NATIVE_SHARD_SCHEMA_VERSIONS))}, got {version!r}"
        )
    decoded = decode_document(shard)
    return [row for reading in decoded["readings"] for row in _reading_segments(reading)]


def project_native_shard(shard: dict) -> dict[str, dict]:
    """Select one canonical timing occasion per verse from a native shard."""
    return _select_occasions(_native_segments(shard))


def _word_reading_segments(reading: dict) -> list[dict]:
    """Parts of one word-profile reading, as canonical timed segments.

    ``words`` rows are positional ``(ref, text, start_ms, end_ms)`` and a part
    addresses them by ``(first_word_index, word_count)`` — the same contiguous
    slice the native profile expresses as ``word_ids``.
    """
    rows = reading["words"]
    out = []
    for ref, start, end, first, count in reading["parts"]:
        timed_words = []
        for word_id in range(first, first + count):
            word_ref, text, word_start, word_end = rows[word_id]
            timed_words.append(
                {
                    "word_id": word_id,
                    "index": _index(word_ref),
                    "ref": word_ref,
                    "source_text": text,
                    "start_ms": int(word_start),
                    "end_ms": int(word_end),
                    # A proxy-timed shard owns no sub-word geometry. Empty is
                    # the honest answer; a consumer that needs letters must
                    # check the profile rather than find a plausible guess.
                    "letters": [],
                }
            )
        out.append({"ref": ref, "t": [int(start), int(end)], "words": timed_words})
    return out


def _word_segments(shard: dict) -> list[dict]:
    version = (shard.get("_meta") or {}).get("schema_version")
    if version != 14:
        raise ValueError(f"word timestamp shard must use schema version 14, got {version!r}")
    return [row for reading in shard["readings"] for row in _word_reading_segments(reading)]


def project_word_shard(shard: dict) -> dict[str, dict]:
    """Select one canonical timing occasion per verse from a word shard."""
    return _select_occasions(_word_segments(shard))


def _segments(shard: dict) -> list[dict]:
    if shard_profile(shard) == "word":
        return _word_segments(shard)
    return _native_segments(shard)


def project_shard(shard: dict) -> dict[str, dict]:
    """Project a shard of either profile — the discriminator decides which."""
    return _select_occasions(_segments(shard))


def project_shard_occurrences(shard: dict) -> list[dict]:
    """Every occurrence in a shard of either profile, in audio order.

    Each row is ``{"ref", "canonical", **projection}`` where the projection is
    the same ``{"words", "verse_start_ms", "verse_end_ms", "segments"}`` body
    ``project_shard`` returns; exactly one row per ref is canonical and equals
    ``project_shard(shard)[ref]``.
    """
    return _all_occasions(_segments(shard))


def select_complete_verses(
    projected: dict[str, dict], word_counts: dict[tuple[int, int], int]
) -> tuple[dict[str, dict], list[str]]:
    """Return canonical verses containing every reference word index."""
    kept, dropped = {}, []
    for ref, verse in projected.items():
        chapter, ayah = map(int, ref.split(":", 1))
        expected = word_counts.get((chapter, ayah))
        covered = {word["index"] for word in verse.get("words") or []}
        if expected and not set(range(1, expected + 1)) <= covered:
            dropped.append(ref)
        else:
            kept[ref] = verse
    return kept, sorted(dropped)


__all__ = [
    "project_native_shard",
    "project_shard",
    "project_shard_occurrences",
    "project_word_shard",
    "select_complete_verses",
]
