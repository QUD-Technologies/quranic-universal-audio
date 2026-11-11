"""``_ayah_of`` files a shard part under its ayah, and never files a special part."""

from __future__ import annotations

import pytest

from services.reference.timestamps import _ayah_of


@pytest.mark.parametrize(
    ("ref", "ayah"),
    [("2:7", 7), ("114:6", 6), ("0:1", None), ("0:2", None), ("2:0", None), ("Basmala", None)],
)
def test_only_verse_parts_have_an_ayah(ref, ayah):
    assert _ayah_of(ref) == ayah
