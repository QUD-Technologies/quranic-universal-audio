"""Decode the compact renderer/timing payload stored in native shards (schema 13–15).

A v15 reading's ``variants`` pass through unchanged."""

from __future__ import annotations

from typing import Any

CODEC_VERSION = 1

_ROLES = ("letter", "haraka", "sukun", "tanween", "madd", "stop_sign", "gap")
_TIERS = ("main", "above", "below")
_STATUSES = ("present", "inserted", "replaced", "dropped", "gap")
_SIDES = ("before", "after")
_STATES = ("start", "join", "sakt", "stop")
_GROUP_KINDS = ("base", "vowel")
_ANIMATION_POLICIES = ("timed", "cohighlight_previous", "cohighlight_next")


class TimestampCodecError(ValueError):
    pass


def _at(values: tuple[str, ...], index: int, label: str) -> str:
    try:
        return values[index]
    except (IndexError, TypeError) as exc:
        raise TimestampCodecError(f"invalid {label} code {index!r}") from exc


def _silence(value: int | None) -> int | str | None:
    if value == -1:
        return "orthographic_silence"
    if value == -2:
        return "variant_silence"
    return value


def _column(row: list) -> dict[str, Any]:
    return {
        "id": row[0],
        "role": _at(_ROLES, row[1], "role"),
        "text": row[2],
        "source_character_ids": [],
        "source_unit_ids": row[3],
        "slot_ids": row[4],
        "tier": _at(_TIERS, row[5], "tier"),
        "attached_to_column_id": row[6],
        "status": _at(_STATUSES, row[7], "status"),
        "variant_id": None,
        "variant_choice": None,
        "anchor_unit_id": row[8],
        "side": None if row[9] is None else _at(_SIDES, row[9], "side"),
        "owned_sound_ids": row[10],
        "presented_sound_ids": row[11],
        "rule_occurrence_ids": row[12],
        "silence": _silence(row[13]),
    }


def _sound(row: list, tokens: list[str]) -> dict[str, Any]:
    sound_id = int(row[0])
    try:
        token = tokens[sound_id]
    except IndexError as exc:
        raise TimestampCodecError(f"sound token {sound_id} is absent") from exc
    return {
        "sound_id": row[0],
        "text": token,
        "column_ids": row[1],
        "rule_occurrence_ids": row[2],
    }


def _bridge(row: list, tokens: list[str]) -> dict[str, Any]:
    return {
        "merger_id": row[0],
        "before_column_ids": row[1],
        "after_column_ids": row[2],
        "sound": _sound(row[3], tokens),
    }


def _word(row: list, word_id: int, tokens: list[str]) -> dict[str, Any]:
    return {
        "word_id": word_id,
        "location": row[0],
        "display_text": row[1],
        "columns": [_column(one) for one in row[2]],
        "sounds": [_sound(one, tokens) for one in row[3]],
        "groups": [
            {
                "key": one[0],
                "kind": _at(_GROUP_KINDS, one[1], "group kind"),
                "column_ids": one[2],
                "sound_ids": one[3],
            }
            for one in row[4]
        ],
        "runs": [{"id": one[0], "source_unit_id": one[1], "column_ids": one[2]} for one in row[5]],
        "bridges": [_bridge(one, tokens) for one in row[6]],
    }


def _boundary(row: list, index: int, tokens: list[str]) -> dict[str, Any]:
    return {
        "boundary_id": index + 1,
        "state": _at(_STATES, row[0], "boundary state"),
        "columns": [_column(one) for one in row[1]],
        "sounds": [_sound(one, tokens) for one in row[2]],
        "bridges": [_bridge(one, tokens) for one in row[3]],
        "verse_end": row[4],
        "exclusive_group": row[5],
    }


def _parts(rows: list[list]) -> list[dict[str, Any]]:
    return [
        {
            "ref": row[0],
            "t": [row[1], row[2]],
            "word_ids": list(range(int(row[3]), int(row[3]) + int(row[4]))),
        }
        for row in rows
    ]


def _boundaries(parts: list[dict], words: list[list], states: list[str]) -> list[dict]:
    if not words:
        return []
    first, last = int(parts[0]["t"][0]), int(parts[-1]["t"][1])
    out: list[dict[str, Any]] = [
        {"boundary_id": 0, "start_ms": first, "end_ms": max(first, words[0][0])}
    ]
    for index in range(1, len(words)):
        start, end = int(words[index - 1][1]), int(words[index][0])
        out.append({"boundary_id": index, "start_ms": start, "end_ms": max(start, end)})
    out.append(
        {
            "boundary_id": len(words),
            "start_ms": int(words[-1][1]),
            "end_ms": max(int(words[-1][1]), last),
        }
    )
    for row, state in zip(out[1:], states, strict=True):
        row["state"] = state
    return out


def _native_result(render: dict, words: list[dict], boundaries: list[dict]) -> dict:
    tokens = render["p"]
    occurrences: dict[int, set[int]] = {}
    sound_words: dict[int, int] = {}
    for word in words:
        for sound in word["sounds"]:
            sound_id = int(sound["sound_id"])
            sound_words[sound_id] = int(word["word_id"])
            occurrences.setdefault(sound_id, set()).update(sound["rule_occurrence_ids"])
    owners = [*words, *boundaries]
    merger_ids = sorted({int(row["merger_id"]) for owner in owners for row in owner["bridges"]})
    result_words = [
        {
            "id": index,
            "ref": row["location"],
            "text": row["display_text"],
            "before_boundary_id": index,
            "after_boundary_id": index + 1,
            "sound_ids": [one["sound_id"] for one in row["sounds"]],
        }
        for index, row in enumerate(words)
    ]
    return {
        "ref": render["m"][0],
        "schema_version": 2,
        "canon_digest": render["m"][1],
        "words": result_words,
        "sounds": [
            {
                "id": index,
                "order": index,
                "token": token,
                "word_id": sound_words.get(index, 0),
                "rule_occurrence_ids": sorted(occurrences.get(index, set())),
            }
            for index, token in enumerate(tokens)
        ],
        "boundaries": [
            {
                "id": index,
                "before": None if index == 0 else index - 1,
                "after": None if index == len(words) else index,
                "state": "start" if index == 0 else boundaries[index - 1]["state"],
                "stop_sign": None,
            }
            for index in range(len(words) + 1)
        ],
        "rule_occurrences": [
            {"id": index, "rule_id": rule_id} for index, rule_id in enumerate(render["r"])
        ],
        "mergers": [{"id": merger_id} for merger_id in merger_ids],
    }


def decode_reading(reading: dict[str, Any]) -> dict[str, Any]:
    """Expand one stored reading into the stable runtime/native projection."""
    render = reading["render"]
    if render.get("v") != CODEC_VERSION:
        raise TimestampCodecError(
            f"compact codec version {render.get('v')!r}, expected {CODEC_VERSION}"
        )
    tokens = render["p"]
    if len(render["w"]) != len(render["b"]):
        raise TimestampCodecError("compact word and boundary counts differ")
    words = [_word(row, index, tokens) for index, row in enumerate(render["w"])]
    boundaries = [_boundary(row, index, tokens) for index, row in enumerate(render["b"])]
    parts = _parts(reading["parts"])
    timing = reading["timing"]
    word_times = timing["w"]
    states = [row["state"] for row in boundaries]
    animation_tokens = [
        {
            "id": index,
            "word_id": meta[0],
            "source_unit_ids": meta[1],
            "character_ids": meta[2],
            "paint_character_ids": meta[3],
            "text": meta[4],
            "sound_ids": meta[5],
            "policy": _at(_ANIMATION_POLICIES, meta[6], "animation policy"),
            "target_token_id": meta[7],
            "start_ms": span[0],
            "end_ms": span[1],
        }
        for index, (meta, span) in enumerate(zip(render["a"], timing["a"], strict=True))
    ]
    result = _native_result(render, words, boundaries)
    return {
        "id": reading["id"],
        "parts": parts,
        "analysis": {"schema_version": 2, "result": result},
        "source": {
            "schema_version": 2,
            "source": {
                "text": " ".join(row["display_text"] for row in words),
                "animation_tokens": animation_tokens,
            },
        },
        "cells": {"schema_version": 2, "cell_view": {"words": words, "boundaries": boundaries}},
        "timing": {
            "words": [
                {"word_id": index, "start_ms": row[0], "end_ms": row[1]}
                for index, row in enumerate(word_times)
            ],
            "sounds": [
                {"sound_id": index, "start_ms": row[0], "end_ms": row[1]}
                for index, row in enumerate(timing["s"])
            ],
            "animation_tokens": animation_tokens,
            "columns": [
                {"column_id": row[0], "start_ms": row[1], "end_ms": row[2]} for row in timing["c"]
            ],
            "boundaries": _boundaries(parts, word_times, states),
        },
        "native_digest": render["m"][2],
        **({"variants": reading["variants"]} if reading.get("variants") else {}),
    }


def decode_document(document: dict[str, Any]) -> dict[str, Any]:
    """Return a document with compact readings expanded for Python consumers."""
    readings = [decode_reading(one) for one in document["readings"]]
    ordered = sorted(readings, key=lambda row: row["parts"][0]["t"][0])
    for current, following in zip(ordered, ordered[1:], strict=False):
        boundary = current["timing"]["boundaries"][-1]
        following_words = following["timing"]["words"]
        following_start = (
            following_words[0]["start_ms"] if following_words else following["parts"][0]["t"][0]
        )
        boundary["end_ms"] = max(boundary["end_ms"], following_start)
    return {**document, "readings": readings}


__all__ = [
    "CODEC_VERSION",
    "TimestampCodecError",
    "decode_document",
    "decode_reading",
]
