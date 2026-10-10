"""Persisted classifier-field stamping — one implementation for every writer.

``qalqala_letter`` is persisted on each segment so the validate pass reads it
instead of recomputing per request. It is a pure function of ``matched_ref``
plus reference data, so a writer that rewrites a ref must re-stamp, and
re-stamping an unchanged segment yields the same value.

Two writers stamp through here: the live save path, segment by segment as an
edit rewrites a ref (``services/segments/save.py``), and the pipeline-run
promoter, whole entries at once.

``source_ref`` and ``projection_support`` ride along for the same reason: they
are pure functions of ``matched_ref`` too, and a ref edit invalidates them (see
``projection_stamp``).

Both entry points take the delivery's SDK riwayah, because the qalqala letter
is read from that edition's word map. Stamping a Warsh delivery under Hafs
persists a wrong letter that the validate pass then trusts without recomputing.
"""

from __future__ import annotations

from qua_shared.riwayat import DEFAULT_SDK_RIWAYAH
from services.segments.projection_stamp import stamp_projection
from services.segments.qalqala import compute_qalqala_letter


def stamp_segment(seg: dict, riwayah: str = DEFAULT_SDK_RIWAYAH) -> None:
    """Stamp the persisted classifier fields and the coordinate provenance in place."""
    seg["qalqala_letter"] = compute_qalqala_letter(seg, riwayah)
    stamp_projection(seg, riwayah)


def stamp_entries(entries: list[dict], *, riwayah: str = DEFAULT_SDK_RIWAYAH) -> int:
    """Stamp every segment of every entry in *entries*; return the count stamped."""
    stamped = 0
    for entry in entries:
        for seg in entry.get("segments", []):
            stamp_segment(seg, riwayah)
            stamped += 1
    return stamped
