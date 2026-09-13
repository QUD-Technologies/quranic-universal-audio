"""Shared per-verse timing layout — the single reconstruction both release
channels consume so they cannot drift.

The HF dataset (``qua_jobs/publish_hf.py``) and the GitHub release
(``qua_jobs/cut_release.py``) used to reconstruct verse geometry independently.
They agree on the source (the canonical native-v13 timing projection) but had two copies of the loader, the
projection→rows reshape, and the clip-window math — which is exactly where they
drifted. This module owns all three so there is one source of truth.

``build_verse_layouts`` returns, per verse, SOURCE-relative ms:

  - ``clip_start`` / ``clip_end`` — the padded verse window. Outer headroom is
    carved from the real inter-verse silence with a ratio-preserving fit
    (``_fit_boundary``) so adjacent verse windows keep >= ``min_gap`` and never
    overlap. This is the audio clip in the HF dataset AND the verse bound in the
    GH release, computed once — so the two channels agree by construction.
  - ``segments`` — post-MFA byte-exact segments (gapless within a segment, a gap
    only across boundaries). Boundaries come from the projection's per-segment
    occurrence spans so a repeated boundary word can't collapse to the wrong
    occurrence. The two outer segments are stretched to the clip edges so they
    absorb the pad while every internal boundary stays byte-exact with a word.
    The HF dataset publishes these; the GH release uses them only to validate
    gaplessness (it does not publish a segment tier).
  - ``words`` / ``tokens`` — timing plus exact DigitalKhatt scalar paint ranges.

The HF dataset rebases these to clip-relative and slices audio; the GH release
keeps them source-relative. Neither re-derives the geometry.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import brotli

from qua_shared.digital_khatt import project_word
from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH


class PadParams(TypedDict):
    """The three clip-edge knobs (ms), shared by both release channels — typed so
    ``**pads`` unpacking into ``build_verse_layouts`` is keyword-exact."""

    pad_start: int
    pad_end: int
    min_gap: int


#: Default clip-edge knobs (ms). pad_start before the first word, pad_end after
#: the last word, min_gap minimum silence kept between adjacent verse clips.
PAD_DEFAULTS: PadParams = {"pad_start": 100, "pad_end": 300, "min_gap": 100}


def pad_params_from_env() -> PadParams:
    """The three release clip-edge knobs from the job env — shared by both
    channels so a "Release settings" change reaches the HF publish AND the GH
    cut identically. Unset/empty falls back to ``PAD_DEFAULTS``.
    """
    return {
        "pad_start": int(
            os.environ.get("PUBLISH_PAD_START", "").strip() or PAD_DEFAULTS["pad_start"]
        ),
        "pad_end": int(os.environ.get("PUBLISH_PAD_END", "").strip() or PAD_DEFAULTS["pad_end"]),
        "min_gap": int(os.environ.get("PUBLISH_MIN_GAP", "").strip() or PAD_DEFAULTS["min_gap"]),
    }


def _i(x) -> int:
    """Coerce ms to int (round on float, pass-through on int)."""
    return int(round(x)) if isinstance(x, float) else int(x)


def _fit_boundary(
    left_end: int, right_start: int, pad_end: int, pad_start: int, min_gap: int
) -> tuple[float, float]:
    """Split the true silence between two adjacent verses (verse N's MFA
    last-letter end → verse N+1's MFA first-letter start) into
    ``(tail_pad for N, lead_pad for N+1)``.

    Hands ``pad_end`` to N's tail and ``pad_start`` to N+1's lead while keeping
    at least ``min_gap`` of silence between the two clips. When they would
    overflow the available silence the pads scale down proportionally (the
    ``pad_end : pad_start`` ratio is preserved) so the clips never leak into each
    other (their gap then equals exactly ``min_gap``).
    """
    sil = right_start - left_end
    if pad_end + min_gap + pad_start <= sil:
        return float(pad_end), float(pad_start)
    budget = max(0.0, sil - min_gap)
    total = pad_end + pad_start
    if total <= 0:
        return 0.0, 0.0
    return budget * pad_end / total, budget * pad_start / total


def _load_audited_shards(ts_dir: Path) -> tuple[list[dict], dict]:
    """Every chapter shard in ``ts_dir``, audited, plus the delivery's ``_meta``.

    The meta names the profile and edition every shard agreed on: the deepest
    tier a delivery can emit, and which script its words are written in, both
    follow from it. A directory mixing profiles or editions is a corrupt
    delivery, not something to merge — the tiers and the script would differ
    per chapter.
    """
    from qua_shared.timestamps_shards import shard_profile
    from qua_shared.timestamps_v13_audit import audit_v13_document
    from qua_shared.timestamps_word_audit import audit_word_document

    documents: list[dict] = []
    if not ts_dir.exists():
        return documents, {}
    profiles: set[str] = set()
    riwayat: set[str] = set()
    for path in sorted(
        ts_dir.iterdir(),
        key=lambda p: int(p.name.split(".", 1)[0]) if p.name.split(".", 1)[0].isdigit() else 0,
    ):
        name = path.name
        if not (name.endswith(".json") or name.endswith(".json.br")):
            continue
        raw = path.read_bytes()
        if name.endswith(".br"):
            raw = brotli.decompress(raw)
        document = json.loads(raw)
        profile = shard_profile(document)
        profiles.add(profile)
        if profile == "word":
            audit_word_document(document)
            riwayat.add(document["_meta"]["riwayah"])
        else:
            audit_v13_document(document)
            riwayat.add(DEFAULT_SDK_RIWAYAH)
        documents.append(document)
    if len(profiles) > 1 or len(riwayat) > 1:
        raise ValueError(
            f"{ts_dir} mixes shard profiles {sorted(profiles)} / editions {sorted(riwayat)} — "
            "one delivery is one edition"
        )
    meta = {"profile": next(iter(profiles)), "riwayah": next(iter(riwayat))} if documents else {}
    return documents, meta


def load_canonical_verses(ts_dir: Path) -> dict[str, dict]:
    """Project every chapter shard in ``ts_dir`` into canonical verse timings.

    The result carries a ``"_meta"`` entry (see ``_load_audited_shards``);
    downstream adapters already skip ``_``-prefixed keys.
    """
    from qua_shared.timestamps_native import project_shard

    documents, meta = _load_audited_shards(ts_dir)
    out: dict[str, dict] = {}
    for document in documents:
        out.update(project_shard(document))
    if out:
        out["_meta"] = meta
    return out


def load_shard_occurrences(ts_dir: Path) -> list[dict]:
    """Every occurrence of every verse across ``ts_dir``, chapter by chapter in
    audio order — ``project_shard_occurrences`` rows, the canonical one per ref
    flagged. The same audits as ``load_canonical_verses`` run first."""
    from qua_shared.timestamps_native import project_shard_occurrences

    documents, _meta = _load_audited_shards(ts_dir)
    return [row for document in documents for row in project_shard_occurrences(document)]


def reshape_canonical(canonical: dict, digital_khatt_words: dict) -> dict[str, dict]:
    """Convert the canonical verse-map shape into ``build_verse_layouts`` input.

    ``canonical[ref]`` is the projection body
    (``{"words": [{"index", "start_ms", "end_ms", "letters"}, ...], "verse_start_ms",
    "verse_end_ms", "segments"}``). For each verse this returns
    ``{"words": [[widx, s, e], ...], "word_texts": [...],
    "tokens_by_word": [...],
    "verse_start_ms": int, "verse_end_ms": int, "seg_spans": [...] | None}``.
    """
    ts: dict[str, dict] = {}
    for ref, val in canonical.items():
        if ref.startswith("_"):
            continue
        words = val.get("words") or []
        if not words:
            ts[ref] = {
                "words": [],
                "word_texts": [],
                "tokens_by_word": [],
                "verse_start_ms": 0,
                "verse_end_ms": 0,
                "seg_spans": [],
            }
            continue
        vs = val["verse_start_ms"]
        ve = val["verse_end_ms"]
        slim_words = [
            [int(word["index"]), int(word["start_ms"]), int(word["end_ms"])] for word in words
        ]
        word_texts: list[str] = []
        tokens_by_word: list[list[dict]] = []
        for word in words:
            if not word.get("letters"):
                # Word-profile: the shard carries the edition's exact text and
                # there is no letter geometry to project onto it. Looking the
                # ref up in the Hafs Digital Khatt map would render another
                # edition's spelling under these coordinates.
                word_texts.append(word["source_text"])
                tokens_by_word.append([])
                continue
            entry = digital_khatt_words.get(word["ref"])
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                raise ValueError(f"DigitalKhatt word {word['ref']} is missing")
            text, tokens = project_word(word, entry["text"])
            word_texts.append(text)
            tokens_by_word.append(tokens)
        ts[ref] = {
            "words": slim_words,
            "word_texts": word_texts,
            "tokens_by_word": tokens_by_word,
            "verse_start_ms": int(vs),
            "verse_end_ms": int(ve),
            "seg_spans": val.get("segments") or [],
        }
    return ts


def build_verse_layouts(
    timestamps: dict,
    *,
    pad_start: int = 100,
    pad_end: int = 300,
    min_gap: int = 100,
    seg_word_range: Callable[[str, str, int], tuple[int, int] | None] | None = None,
    detailed_by_ref: dict | None = None,
) -> dict[str, dict]:
    """Build SOURCE-relative per-verse layouts from the reshaped canonical map.

    ``timestamps`` is ``reshape_canonical`` output. ``seg_word_range`` +
    ``detailed_by_ref`` are only consulted for legacy inputs that carry no
    occurrence spans (``seg_spans``); the real projection path always carries
    them. Returns ``{ref: {clip_start, clip_end, verse_start, verse_end,
    segments, words, letters}}`` — see the module docstring.
    """
    detailed_by_ref = detailed_by_ref or {}
    layouts: dict[str, dict] = {}
    for ref, tdata in timestamps.items():
        if ref.startswith("_"):
            continue
        words = tdata.get("words") or []
        if not words:
            continue
        try:
            surah_num, ayah_s = ref.split(":", 1)
            ayah = int(ayah_s)
        except (ValueError, AttributeError):
            continue
        verse_start = int(tdata["verse_start_ms"])
        verse_end = int(tdata["verse_end_ms"])

        # Clip edges from MFA times (not VAD): pad the verse's outer edges with
        # ratio-fit headroom shared against the neighbour verses, so adjacent
        # windows keep >= min_gap of silence and never leak. Chapter-edge verses
        # just pad. (Clamp to the source start; the upper clamp to audio length
        # is the slicer's job — the layout stays geometry-only.)
        prev = timestamps.get(f"{surah_num}:{ayah - 1}")
        nxt = timestamps.get(f"{surah_num}:{ayah + 1}")
        if prev and prev.get("words"):
            _, lead_pad = _fit_boundary(
                int(prev["verse_end_ms"]), verse_start, pad_end, pad_start, min_gap
            )
        else:
            lead_pad = float(pad_start)
        if nxt and nxt.get("words"):
            tail_pad, _ = _fit_boundary(
                verse_end, int(nxt["verse_start_ms"]), pad_end, pad_start, min_gap
            )
        else:
            tail_pad = float(pad_end)
        clip_start = max(0, int(round(verse_start - lead_pad)))
        clip_end = int(round(verse_end + tail_pad))

        # Segments: prefer the projection's per-segment OCCURRENCE spans (a
        # repeated boundary word can't collapse to the wrong occurrence). Fall
        # back to the word-index map for legacy word-only inputs.
        seg_spans = tdata.get("seg_spans")
        segments: list[list[int]] = []
        if seg_spans:
            for sp in seg_spans:
                segments.append(
                    [
                        _i(sp["w_from"]),
                        _i(sp["w_to"]),
                        _i(int(sp["start_ms"])),
                        _i(int(sp["end_ms"])),
                    ]
                )
        elif seg_word_range is not None:
            word_by_idx = {int(w[0]): (int(w[1]), int(w[2])) for w in words}
            for seg in (detailed_by_ref.get(ref) or {}).get("segments", []) or []:
                wr = seg_word_range(seg.get("matched_ref", ""), surah_num, ayah)
                if wr is None:
                    continue
                w_from, w_to = wr
                if w_from not in word_by_idx or w_to not in word_by_idx:
                    continue
                segments.append(
                    [_i(w_from), _i(w_to), _i(word_by_idx[w_from][0]), _i(word_by_idx[w_to][1])]
                )

        verse_words = [[_i(w[0]), _i(w[1]), _i(w[2])] for w in words]

        # Synthesize missing segments around the home segments (cross-verse).
        if verse_words and not segments:
            segments.append(
                [verse_words[0][0], verse_words[-1][0], verse_words[0][1], verse_words[-1][2]]
            )
        elif verse_words and segments:
            first_seg_start = segments[0][2]
            xv_before = [w for w in verse_words if w[2] <= first_seg_start]
            if xv_before:
                segments.insert(
                    0,
                    [xv_before[0][0], xv_before[-1][0], xv_before[0][1], xv_before[-1][2]],
                )
            last_seg_end = segments[-1][3]
            xv_after = [w for w in verse_words if w[1] >= last_seg_end]
            if xv_after:
                segments.append([xv_after[0][0], xv_after[-1][0], xv_after[0][1], xv_after[-1][2]])

        # Outer-edge headroom: the first segment starts at the clip start and the
        # last ends at the clip end, so the two outer segments absorb the lead/
        # tail pads while every internal boundary stays byte-exact with a word.
        if segments:
            segments[0][2] = clip_start
            segments[-1][3] = clip_end

        word_texts = tdata.get("word_texts") or []
        tokens_by_word = tdata.get("tokens_by_word") or []
        if len(word_texts) != len(verse_words) or len(tokens_by_word) != len(verse_words):
            raise ValueError(f"DigitalKhatt projection count differs from words in {ref}")
        verse_text = " ".join(word_texts)
        verse_tokens: list[tuple[int, int, int, bool, list[list[int]]]] = []
        scalar_cursor = 0
        for occurrence, (word_text, tokens) in enumerate(
            zip(word_texts, tokens_by_word, strict=True)
        ):
            for token in tokens:
                if token["start_ms"] is None or token["end_ms"] is None:
                    raise ValueError(f"animation token in {ref} has unplaced timing")
                verse_tokens.append(
                    (
                        occurrence,
                        int(token["start_ms"]),
                        int(token["end_ms"]),
                        bool(token["owns_sound"]),
                        [
                            [scalar_cursor + int(start), scalar_cursor + int(end)]
                            for start, end in token["paint"]
                        ],
                    )
                )
            scalar_cursor += len(word_text) + 1

        layouts[ref] = {
            "clip_start": clip_start,
            "clip_end": clip_end,
            "verse_start": verse_start,
            "verse_end": verse_end,
            "segments": segments,
            "words": verse_words,
            "text": verse_text,
            "tokens": verse_tokens,
        }
    return layouts
