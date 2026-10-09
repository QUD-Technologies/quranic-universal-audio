"""DigitalKhatt presentation mapping for public timestamp projections."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher
from functools import cache

DIGITAL_KHATT_SCRIPT_ID = "digital_khatt_v2"
DIGITAL_KHATT_SCRIPT_FILENAME = "digital_khatt_v2_script.json"
DIGITAL_KHATT_FONT_FILENAME = "DigitalKhattV2.otf"
UNICODE_INDEXING = "scalar"

# Presentation-only marks stay in the released text but own no timed paint.
DISPLAY_ONLY_MARKS = frozenset(
    chr(codepoint)
    for codepoint in (
        0x06D6,  # small high sad-lam-alef-maqsura
        0x06D7,  # small high qaf-lam-alef-maqsura
        0x06D8,  # small high meem initial form
        0x06D9,  # small high lam alef (DK writes none today)
        0x06DA,  # small high jeem
        0x06DB,  # small high three dots
        0x06DE,  # rub el hizb
        0x06E9,  # place of sajdah
        0x06EC,  # rounded high stop
    )
)

_BLOCKED = -1


def _expanded(
    text: str, owners: Sequence[int | None]
) -> tuple[list[str], list[int | None], list[int]]:
    chars: list[str] = []
    expanded_owners: list[int | None] = []
    origins: list[int] = []
    for index, char in enumerate(text):
        for scalar in unicodedata.normalize("NFD", char):
            chars.append(scalar)
            expanded_owners.append(owners[index] if index < len(owners) else None)
            origins.append(index)
    return chars, expanded_owners, origins


@cache
def _align_scalar_owners_cached(
    source_text: str, source_owners: tuple[int | None, ...], presentation_text: str
) -> tuple[int | None, ...]:
    source, owners, _ = _expanded(source_text, source_owners)
    presented, _, origins = _expanded(presentation_text, [None] * len(presentation_text))
    expanded: list[int | None] = [None] * len(presented)
    matcher = SequenceMatcher(a=source, b=presented, autojunk=False)
    for tag, source_at, source_end, presented_at, presented_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(source_end - source_at):
                expanded[presented_at + offset] = owners[source_at + offset]
        elif tag == "replace" and source_end - source_at == presented_end - presented_at:
            for offset in range(source_end - source_at):
                expanded[presented_at + offset] = owners[source_at + offset]

    result: list[int | None] = [None] * len(presentation_text)
    for owner, origin in zip(expanded, origins, strict=True):
        if owner is not None:
            result[origin] = owner
    for index, char in enumerate(presentation_text):
        if (
            result[index] is not None
            or char in DISPLAY_ONLY_MARKS
            or not unicodedata.category(char).startswith("M")
        ):
            continue
        result[index] = next(
            (result[at] for at in range(index - 1, -1, -1) if result[at] is not None),
            next(
                (result[at] for at in range(index + 1, len(result)) if result[at] is not None),
                None,
            ),
        )
    return tuple(None if owner == _BLOCKED else owner for owner in result)


def align_scalar_owners(
    source_text: str, source_owners: list[int | None], presentation_text: str
) -> list[int | None]:
    """Transfer source scalar ownership onto an exact DigitalKhatt word.

    The script and producer ownership pattern repeat across reciters, so cache
    this pure alignment for the multi-reciter release cut.
    """
    return list(_align_scalar_owners_cached(source_text, tuple(source_owners), presentation_text))


def token_scalar_owners(source_text: str, tokens: list[dict]) -> list[int | None]:
    """Build a source-word owner per Unicode scalar from v13 token identities."""
    owners: list[int | None] = [None] * len(source_text)
    for token_index, token in enumerate(tokens):
        characters = {int(value) for value in token["character_offsets"]}
        painted = {int(value) for value in token["paint_character_offsets"]}
        for index in characters - painted:
            if not 0 <= index < len(owners):
                raise ValueError(f"character offset {index} is outside {source_text!r}")
            owners[index] = _BLOCKED
        for index in painted:
            if not 0 <= index < len(owners):
                raise ValueError(f"paint offset {index} is outside {source_text!r}")
            if owners[index] not in (None, token_index):
                raise ValueError(f"source scalar {index} has multiple animation owners")
            owners[index] = token_index

    for index in range(len(owners)):
        if owners[index] is not None:
            continue
        owners[index] = next(
            (owners[at] for at in range(index - 1, -1, -1) if owners[at] is not None),
            next(
                (owners[at] for at in range(index + 1, len(owners)) if owners[at] is not None),
                None,
            ),
        )
    return owners


def scalar_ranges(owners: list[int | None], owner: int) -> list[list[int]]:
    """Return compact half-open ranges for one owner."""
    ranges: list[list[int]] = []
    start: int | None = None
    for index, value in enumerate([*owners, None]):
        if value == owner and start is None:
            start = index
        elif value != owner and start is not None:
            ranges.append([start, index])
            start = None
    return ranges


def project_word(word: dict, presentation_text: str) -> tuple[str, list[dict]]:
    """Project one canonical v13 word onto DigitalKhatt scalar paint ranges."""
    tokens = word.get("letters") or []
    source_text = word["source_text"]
    source_owners = token_scalar_owners(source_text, tokens)

    base_text = "".join(char for char in presentation_text if char not in DISPLAY_ONLY_MARKS)
    base_owners = iter(align_scalar_owners(source_text, source_owners, base_text))
    presented_owners = [
        None if char in DISPLAY_ONLY_MARKS else next(base_owners) for char in presentation_text
    ]

    projected: list[dict] = []
    for token_index, token in enumerate(tokens):
        ranges = scalar_ranges(presented_owners, token_index)
        if not ranges:
            raise ValueError(
                f"animation token {token_index} in {word['ref']} has no DigitalKhatt paint range"
            )
        projected.append(
            {
                "start_ms": token["start_ms"],
                "end_ms": token["end_ms"],
                "owns_sound": bool(token["sound_ids"]),
                "paint": ranges,
            }
        )
    return presentation_text, projected


__all__ = [
    "DIGITAL_KHATT_FONT_FILENAME",
    "DIGITAL_KHATT_SCRIPT_FILENAME",
    "DIGITAL_KHATT_SCRIPT_ID",
    "DISPLAY_ONLY_MARKS",
    "UNICODE_INDEXING",
    "align_scalar_owners",
    "project_word",
    "scalar_ranges",
    "token_scalar_owners",
]
