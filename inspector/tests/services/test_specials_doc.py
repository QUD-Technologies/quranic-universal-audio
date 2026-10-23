"""specials.json built from an align run's strip events and kept rows."""

from __future__ import annotations

from qua_shared.schemas.bucket.specials import SpecialsDoc
from qua_shared.schemas.bucket.staged_run import PipelineAuditEvent, PipelineSegSnapshot
from services.segments.specials_doc import specials_from_run as _from_run


def _delete(chapter: int, ref: str, start: int, end: int, conf: float = 1.0) -> PipelineAuditEvent:
    snap = PipelineSegSnapshot(
        index_at_save=0, time_start=start, time_end=end, matched_ref=ref, confidence=conf
    )
    return PipelineAuditEvent(kind="delete_segment", chapter=chapter, targets_before=[snap])


def specials_from_run(*args, **kwargs) -> SpecialsDoc:
    doc = _from_run(*args, **kwargs)
    assert doc is not None
    return doc


def _chapter(*rows: tuple[str, int, int]) -> dict:
    return {"segments": [{"matched_ref": r, "time_start": a, "time_end": b} for r, a, b in rows]}


def test_pre_ayah_openers_become_align_specials_in_time_order():
    by_chapter = {2: _chapter(("2:1:1-2:1:1", 9000, 12000), ("2:2:1-2:2:5", 12500, 20000))}
    events = [_delete(2, "Basmala", 4000, 8800), _delete(2, "Isti'adha", 200, 3900, 0.9)]
    doc = specials_from_run(events, by_chapter, generated_at="t", riwayah="hafs")
    items = doc.chapters["2"]
    assert [(s.type, s.start_ms, s.end_ms, s.source, s.position) for s in items] == [
        ("Isti'adha", 200, 3900, "align", "pre"),
        ("Basmala", 4000, 8800, "align", "pre"),
    ]
    assert items[0].confidence == 0.9


def test_an_opener_running_into_ayah_1_is_clipped():
    by_chapter = {50: _chapter(("50:1:1-50:1:1", 4090, 8170))}
    doc = specials_from_run(
        [_delete(50, "Basmala", 0, 8495)], by_chapter, generated_at="t", riwayah="hafs"
    )
    only = doc.chapters["50"][0]
    assert (only.end_ms, only.clipped) == (4090, True)


def test_trailing_sadaqa_is_kept_as_post_and_mid_chapter_rows_are_dropped():
    by_chapter = {3: _chapter(("3:1:1-3:1:1", 5000, 6000), ("3:200:1-3:200:9", 6000, 90000))}
    events = [_delete(3, "Sadaqa", 90100, 92000), _delete(3, "Basmala", 40000, 44000)]
    doc = specials_from_run(events, by_chapter, generated_at="t", riwayah="hafs")
    assert [(s.type, s.position) for s in doc.chapters["3"]] == [("Sadaqa", "post")]


def test_fatiha_verse_1_1_is_listed_in_numbering_editions_only():
    fatiha = {1: _chapter(("1:1:1-1:1:4", 3300, 6200), ("1:2:1-1:2:4", 6300, 9000))}
    hafs = specials_from_run([], fatiha, generated_at="t", riwayah="hafs")
    one = hafs.chapters["1"][0]
    assert (one.type, one.source, one.start_ms, one.end_ms) == (
        "Basmala",
        "fatiha_verse",
        3300,
        6200,
    )
    warsh = _from_run(
        [], {1: _chapter(("1:1:1-1:1:4", 3300, 6200))}, generated_at="t", riwayah="warsh"
    )
    assert warsh is None


def test_unknown_special_names_are_ignored():
    by_chapter = {2: _chapter(("2:1:1-2:1:1", 9000, 12000))}
    assert (
        _from_run([_delete(2, "special", 0, 800)], by_chapter, generated_at="t", riwayah="hafs")
        is None
    )


def test_the_aligner_voice_verdict_lands_on_the_fatiha_basmala():
    fatiha = {1: _chapter(("1:1:1-1:1:4", 3300, 6200), ("1:2:1-1:2:4", 6300, 9000))}
    voice = {"verdict": "needs_confirmation", "z": 1.93, "version": 1}
    one = specials_from_run([], fatiha, generated_at="t", riwayah="hafs", voice=voice).chapters[
        "1"
    ][0]
    assert (one.voice_verdict, one.voice_z) == ("needs_confirmation", 1.93)


def test_an_unavailable_voice_verdict_records_nothing():
    fatiha = {1: _chapter(("1:1:1-1:1:4", 3300, 6200))}
    voice = {"verdict": "unavailable", "z": None, "reason": "too_few_references", "version": 1}
    one = specials_from_run([], fatiha, generated_at="t", riwayah="hafs", voice=voice).chapters[
        "1"
    ][0]
    assert (one.voice_verdict, one.voice_z) == (None, None)
