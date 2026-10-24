"""Pre-shape every Hafs DK word into chapter-local token-addressable glyphs."""

from __future__ import annotations

import json
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import uharfbuzz as _hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from quranic_phonemizer import Phonemizer

hb: Any = _hb

ROOT = Path(__file__).resolve().parents[2]
FONT = ROOT / "inspector/frontend/public/fonts/DigitalKhattV2.otf"
DK = ROOT / "data/digital_khatt_v2_script.json"
OUTPUT_DIR = ROOT / "inspector/frontend/public/generated/shaped-glyphs-v13"
SPECIAL = {
    0x0654: ("hamzaabove",),
    0x0655: ("hamzabelow",),
    0x0670: ("smallalef",),
    0x06E5: ("smallwaw",),
    0x06E6: ("smallyeh",),
    0x06E7: ("smallhighyeh",),
    0x06E8: ("smallhighnoon",),
    0x06DC: ("smallhighseen",),
    0x06E3: ("smalllowseen",),
}
_BLOCKED = -1
STOP_MARKS = {0x06D6, 0x06D7, 0x06D8, 0x06DA, 0x06DB}
SILENT_COMPANION_ROLE = "silent_companion"
#: The verse words an opener's shard words (``0:1:n`` Isti'adha, ``0:2:n`` Basmala) are read as
#: (``qua_sdk.integrations.special_native``): their tokens are these words', so they take these
#: words' shapes. Mirrored by ``OPENER_SOURCE_WORDS`` in the FE ``recitation-data/specials.ts``.
OPENER_SOURCE_WORDS = (
    ("2:67:15", "16:98:5", "16:98:6", "16:98:7", "16:98:8"),
    ("1:1:1", "1:1:2", "1:1:3", "1:1:4"),
)


def _verse_refs(dk: dict[str, dict]) -> tuple[str, ...]:
    """All Hafs verse refs in canonical order, derived from the DK corpus."""
    refs = {tuple(map(int, location.split(":")[:2])) for location in dk}
    return tuple(f"{surah}:{ayah}" for surah, ayah in sorted(refs))


def _path(glyph_set, name: str) -> str:
    pen = SVGPathPen(glyph_set)
    glyph_set[name].draw(pen)
    return pen.getCommands()


def _advance(font: Any, text: str) -> int:
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction, buf.script, buf.language = "rtl", "arab", "ar"
    hb.shape(font, buf)
    return sum(position.x_advance for position in buf.glyph_positions)


def _expanded(text: str, owners: list[int | None]) -> tuple[list[str], list[int | None], list[int]]:
    chars, tokens, origins = [], [], []
    for index, char in enumerate(text):
        for scalar in unicodedata.normalize("NFD", char):
            chars.append(scalar)
            tokens.append(owners[index] if index < len(owners) else None)
            origins.append(index)
    return chars, tokens, origins


def _dk_owners(source_text: str, source_owners: list[int | None], dk_text: str) -> list[int | None]:
    src, tokens, _ = _expanded(source_text, source_owners)
    dst, _, origins = _expanded(dk_text, [None] * len(dk_text))
    expanded: list[int | None] = [None] * len(dst)
    matcher = SequenceMatcher(a=src, b=dst, autojunk=False)
    for tag, src_at, src_end, dst_at, dst_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(src_end - src_at):
                expanded[dst_at + offset] = tokens[src_at + offset]
        elif tag == "replace" and src_end - src_at == dst_end - dst_at:
            for offset in range(src_end - src_at):
                expanded[dst_at + offset] = tokens[src_at + offset]
    owners: list[int | None] = [None] * len(dk_text)
    for token, origin in zip(expanded, origins, strict=True):
        if token is not None:
            owners[origin] = token
    for index, char in enumerate(dk_text):
        if owners[index] is not None or not unicodedata.category(char).startswith("M"):
            continue
        owners[index] = next(
            (owners[at] for at in range(index - 1, -1, -1) if owners[at] is not None),
            next(
                (owners[at] for at in range(index + 1, len(owners)) if owners[at] is not None), None
            ),
        )
    return [None if owner == _BLOCKED else owner for owner in owners]


def opener_words(dk: dict[str, dict], words: dict[str, dict]) -> dict[str, dict]:
    """The shaped words the openers are read as, keyed by DK text like a chapter file."""
    texts = [dk[ref]["text"] for refs in OPENER_SOURCE_WORDS for ref in refs]
    return {text: words[text] for text in texts}


def _write_json(target: Path, payload: dict) -> int:
    target.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return target.stat().st_size


def main() -> None:
    dk = json.loads(DK.read_text(encoding="utf-8"))
    font_bytes = FONT.read_bytes()
    hb_font = hb.Font(hb.Face(font_bytes))
    tt = TTFont(FONT)
    glyph_order, glyph_set = tt.getGlyphOrder(), tt.getGlyphSet()
    paths: dict[str, str] = {}
    # A rendered DK word has one stable token-owner pattern throughout Hafs.
    # Keying by text deduplicates locations within a chapter, while chapter
    # files let Inspector fetch only the active surah instead of parsing a
    # Quran-wide asset at startup. `canonical_words` makes any cross-corpus
    # owner-pattern conflict a hard generation error.
    words_by_chapter: dict[int, dict[str, dict]] = {chapter: {} for chapter in range(1, 115)}
    canonical_words: dict[str, dict] = {}
    location_count = 0
    phonemizer: Any = Phonemizer()
    refs = _verse_refs(dk)
    for ref_index, ref in enumerate(refs, start=1):
        result = phonemizer.analyse(ref)
        source = result.source()
        tokens_by_word = {}
        for token in source.animation_tokens:
            tokens_by_word.setdefault(token.word_id.value, []).append(token)
        chars_by_word = {}
        for char in source.characters:
            if char.word_id is not None:
                chars_by_word.setdefault(char.word_id.value, []).append(char)
        for word in result.words:
            word_id = word.id.value
            location = word.ref
            dk_text = dk[location]["text"]
            base_text = "".join(char for char in dk_text if ord(char) not in STOP_MARKS)
            word_tokens = tokens_by_word[word_id]
            token_by_char = {
                character.value: local_id
                for local_id, token in enumerate(word_tokens)
                for character in token.paint_character_ids
            }
            blocked_chars = {
                character.value
                for token in word_tokens
                for character in token.character_ids
                if character not in token.paint_character_ids
            }
            source_chars = chars_by_word[word_id]
            source_text = "".join(char.text for char in source_chars)
            source_owners = [
                _BLOCKED if char.id.value in blocked_chars else token_by_char.get(char.id.value)
                for char in source_chars
            ]
            for index, _char in enumerate(source_chars):
                if source_owners[index] is not None:
                    continue
                source_owners[index] = next(
                    (
                        source_owners[at]
                        for at in range(index - 1, -1, -1)
                        if source_owners[at] is not None
                    ),
                    next(
                        (
                            source_owners[at]
                            for at in range(index + 1, len(source_owners))
                            if source_owners[at] is not None
                        ),
                        None,
                    ),
                )
            # Stop signs exist only in the DK display string. Align ownership
            # against the shaped base first, then reinsert ownerless sign slots;
            # otherwise SequenceMatcher can fold the final sounding letter and
            # trailing sign into one unequal replace block (e.g. final alif
            # maqsura -> yeh + waqf), leaving that final glyph unpaintable.
            base_owners = iter(_dk_owners(source_text, source_owners, base_text))
            owners = [None if ord(char) in STOP_MARKS else next(base_owners) for char in dk_text]
            special_occurrences = {
                cp: [
                    (index, owners[index])
                    for index, char in enumerate(dk_text)
                    if chr(cp) in unicodedata.normalize("NFD", char) and owners[index] is not None
                ]
                for cp in SPECIAL
            }
            # DigitalKhatt inserts U+06E2 above a written noon for iqlab. The
            # phonemizer correctly gives the resulting meem sound to the noon's
            # animation token, but the display glyph is a three-path ligature:
            # mini-meem + silent noon body + noon dot. Preserve the token owner
            # while labelling the two host paths so silent-omit mode can leave
            # them visible in the base silhouette without painting them active.
            iqlab_owners = {
                owners[index]
                for index in range(len(dk_text) - 1)
                if dk_text[index] == "ن" and dk_text[index + 1] == "ۢ" and owners[index] is not None
            }

            buf = hb.Buffer()
            buf.add_str(dk_text)
            buf.direction, buf.script, buf.language = "rtl", "arab", "ar"
            buf.cluster_level = hb.BufferClusterLevel.CHARACTERS
            hb.shape(hb_font, buf)
            x = 0
            placements = []
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
                name = glyph_order[info.codepoint]
                paths.setdefault(name, _path(glyph_set, name))
                cluster = min(info.cluster, len(owners) - 1)
                token = owners[cluster]
                for cp, prefixes in SPECIAL.items():
                    if any(name.startswith(prefix) for prefix in prefixes):
                        candidates = special_occurrences[cp]
                        if candidates:
                            token = min(candidates, key=lambda row: abs(row[0] - cluster))[1]
                        break
                role = "waqf" if name.startswith("waqf.") else None
                if role is None and token in iqlab_owners and name != "meemiqlab":
                    role = SILENT_COMPANION_ROLE
                placements.append(
                    [
                        name,
                        x + pos.x_offset,
                        950 - pos.y_offset,
                        token,
                        role,
                    ]
                )
                x += pos.x_advance
            if _advance(hb_font, base_text) != x:
                raise ValueError(f"stop-mark removal changed shaped advance for {location}")
            shaped_word = {
                "baseText": base_text,
                "advance": x,
                "tokenCount": len(word_tokens),
                "placements": placements,
            }
            existing = canonical_words.get(dk_text)
            if existing is not None and existing != shaped_word:
                raise ValueError(
                    f"rendered word {dk_text!r} has conflicting token ownership at {location}"
                )
            canonical_words.setdefault(dk_text, shaped_word)
            chapter = int(location.split(":", 1)[0])
            words_by_chapter[chapter].setdefault(dk_text, shaped_word)
            location_count += 1
        if ref_index % 250 == 0 or ref_index == len(refs):
            print(
                f"shaped {ref_index}/{len(refs)} verses "
                f"({location_count} locations, {len(canonical_words)} unique words)",
                flush=True,
            )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths_target = OUTPUT_DIR / "paths.json"
    head: Any = tt["head"]
    units_per_em = head.unitsPerEm
    paths_target.write_text(
        json.dumps(
            {"upem": units_per_em, "paths": dict(sorted(paths.items()))},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    total_bytes = paths_target.stat().st_size
    for chapter, words in words_by_chapter.items():
        payload = json.dumps(
            {
                "upem": units_per_em,
                "words": words,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        target = OUTPUT_DIR / f"{chapter}.json"
        target.write_text(payload, encoding="utf-8")
        total_bytes += target.stat().st_size
    total_bytes += _write_json(
        OUTPUT_DIR / "openers.json",
        {"upem": units_per_em, "words": opener_words(dk, canonical_words)},
    )
    print(
        f"{OUTPUT_DIR}: {location_count} locations, {len(canonical_words)} unique "
        f"words, {len(paths)} glyph outlines, {total_bytes} bytes across shared paths "
        f"and 114 chapters + openers"
    )


if __name__ == "__main__":
    main()
