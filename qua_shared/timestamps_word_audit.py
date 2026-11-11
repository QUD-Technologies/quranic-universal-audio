"""Structural audit for a word-profile timestamp shard (schema 14).

The native v13 audit proves identity closure over cells, sounds and rule
occurrences. A word shard has none of those, so the equivalent guarantees are
different in kind but the same in spirit: every word the parts claim exists,
every interval is ordered and sits inside the part that owns it, no two words
in one reading overlap, and the text came from the edition index the meta
names.

The last check is the important one. A word row carries *exact target text*
that the Segments and Timestamps tabs render directly; if it were allowed to
drift from the edition index the shard would silently show one edition's script
under another's coordinates. ``words_sha256`` pins the index *revision*, which
is not the same claim — so every row's text is compared against the index
itself, ref by ref. Both checks need ``qua_domain``; without it they are
skipped, not weakened, and a Hafs-only deployment never writes a word shard.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from qua_shared.schemas.bucket.ts_shard import TsWordShardDoc

#: Unnumbered special ordinals — the recited Basmala / Isti'adha rows the SDK
#: timing contract emits as ``0:0:N`` rather than inventing target coordinates.
_SPECIAL_REF = re.compile(r"^0:0:[1-9]\d*$")
#: A special reading's word refs: Isti'adha ``0:1:N``, Basmala ``0:2:N``.
_SPECIAL_READING_REF = re.compile(r"^0:[12]:[1-9]\d*$")


class WordAuditError(ValueError):
    """A word-profile shard cannot be trusted and must not be written."""


def _check_reading(reading, riwayah: str, index: dict[str, str] | None) -> None:
    label = f"reading {reading.id!r}"

    if not reading.words:
        raise WordAuditError(f"{label}: has no words")

    covered: set[int] = set()
    for ref, start, end, first, count in reading.parts:
        span = range(first, first + count)
        overlap = covered.intersection(span)
        if overlap:
            raise WordAuditError(f"{label}: parts overlap on word ids {sorted(overlap)[:5]}")
        covered.update(span)
        for word_id in span:
            _, _, w_start, w_end = reading.words[word_id]
            if w_start < start or w_end > end:
                raise WordAuditError(
                    f"{label}: word {word_id} interval [{w_start},{w_end}] "
                    f"escapes its part {ref!r} [{start},{end}]"
                )

    missing = set(range(len(reading.words))) - covered
    if missing:
        raise WordAuditError(f"{label}: words not covered by any part: {sorted(missing)[:5]}")

    previous_end: int | None = None
    for word_id, (ref, text, start, end) in enumerate(reading.words):
        if not text:
            raise WordAuditError(f"{label}: word {word_id} has empty text")
        if previous_end is not None and start < previous_end:
            raise WordAuditError(
                f"{label}: word {word_id} starts at {start} before the previous word ended "
                f"at {previous_end}"
            )
        previous_end = end
        if _SPECIAL_REF.match(ref) or index is None:
            continue
        if reading.special is not None and _SPECIAL_READING_REF.match(ref):
            continue
        expected = index.get(ref)
        if expected is None:
            raise WordAuditError(f"{label}: word {word_id} ref {ref!r} is not a {riwayah} word")
        if text != expected:
            raise WordAuditError(
                f"{label}: word {word_id} ({ref}) carries text the {riwayah} index does "
                f"not write — the shard would render another edition's script"
            )


def audit_word_document(shard_doc: dict) -> TsWordShardDoc:
    """Validate and structurally audit a word-profile shard; return the model.

    Raises :class:`WordAuditError` on any violation. The coordinate check is
    skipped (not weakened) when ``qua_domain`` is unavailable — a Hafs-only
    deployment never writes a word shard, and the producer that does always has
    the package.
    """
    try:
        doc = TsWordShardDoc.model_validate(shard_doc)
    except ValidationError as exc:
        # One exception type for the whole audit: every caller catches
        # WordAuditError, so a schema violation must not slip past as a
        # ValidationError and abort the run somewhere less specific.
        raise WordAuditError(f"word shard failed validation: {exc}") from exc
    if not doc.readings:
        raise WordAuditError("word shard has no readings")

    index = _edition_index(doc.meta.riwayah, doc.meta.words_sha256)
    for reading in doc.readings:
        _check_reading(reading, doc.meta.riwayah, index)

    ids = [reading.id for reading in doc.readings]
    if len(ids) != len(set(ids)):
        raise WordAuditError("duplicate reading id")
    return doc


def _edition_index(riwayah: str, words_sha256: str) -> dict[str, str] | None:
    """``{ref: exact text}`` for ``riwayah``, or ``None`` when unavailable."""
    try:
        from qua_domain import (
            DEFAULT_RIWAYAH,
            SUPPORTED_RIWAYAT,
            get_edition,
            load_edition_index,
            normalize_riwayah,
        )
    except ImportError:
        return None

    try:
        slug = normalize_riwayah(riwayah)
    except ValueError as exc:
        raise WordAuditError(f"{riwayah!r} is not a riwayah this build knows") from exc
    if slug not in SUPPORTED_RIWAYAT:
        raise WordAuditError(f"riwayah {riwayah!r} is not supported")
    if slug == DEFAULT_RIWAYAH:
        raise WordAuditError(
            "a word-profile shard cannot be hafs — the reference edition is timed "
            "natively and the profile would discard its cells and letters"
        )

    edition = get_edition(slug)
    expected = getattr(edition, "words_sha256", None)
    if expected and expected != words_sha256:
        raise WordAuditError(
            f"words_sha256 {words_sha256[:12]}… does not match the installed "
            f"{riwayah} index {expected[:12]}… — the text and the coordinates "
            f"come from different revisions"
        )
    return {word.ref: word.text for word in load_edition_index(slug).words}
