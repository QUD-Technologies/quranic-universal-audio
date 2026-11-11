"""Readings summary builder — v15 shards folded into per-word rows."""

from __future__ import annotations

import json

import pytest

from services.reference import readings

CATALOGUE = {
    "istifham_article": {"options": ["ibdal", "tashil"]},
    "yabsut": {"options": ["seen", "saad"]},
    "yaseen_wasl": {"options": ["izhar", "idgham"]},
    "yaa_aatani_waqf": {"options": ["ithbat", "hadhf"]},
    "alism_ibtidaa": {"options": ["hamza", "lam"]},
    "iwaja_qayyima": {"options": ["sakt", "idraj"]},
    "raa_firq": {"options": ["light", "heavy"]},
}


def _variant(sel, chosen, words, by="scored"):
    return {"id": sel, "chosen": chosen, "words": words, "targets": words, "by": by, "score": 1}


def _reading(words, *variants):
    return {"render": {"w": [[ref, text, []] for ref, text in words]}, "variants": list(variants)}


def _shard(*readings_, version=15):
    return {
        "_meta": {"schema_version": version, "variant_catalogue": CATALOGUE},
        "readings": list(readings_),
    }


SHARDS = {
    2: _shard(_reading([("2:245:14", "وَيَبْصُۜطُ")], _variant("yabsut", "saad", [0], by="default"))),
    6: _shard(_reading([("6:143:10", "ءَآلذَّكَرَيْنِ")], _variant("istifham_article", "ibdal", [0]))),
    10: _shard(
        _reading([("10:51:7", "ءَآلْـَٔـٰنَ")], _variant("istifham_article", "tashil", [0])),
        _reading(
            [("10:59:13", "قُلْ"), ("10:59:14", "ءَآللَّهُ")], _variant("istifham_article", "tashil", [1])
        ),
        _reading([("10:91:1", "ءَآلْـَٔـٰنَ")], _variant("istifham_article", "tashil", [0])),
    ),
    18: _shard(
        _reading(
            [("18:1:11", "عِوَجَاۜ"), ("18:2:1", "قَيِّمًا")], _variant("iwaja_qayyima", "sakt", [0, 1])
        ),
        _reading(
            [("18:1:11", "عِوَجَاۜ"), ("18:2:1", "قَيِّمًا")], _variant("iwaja_qayyima", "sakt", [0, 1])
        ),
    ),
    27: _shard(
        _reading([("27:36:8", "ءَاتَىٰنِ")], _variant("yaa_aatani_waqf", "hadhf", [0])),
        _reading([("27:59:9", "ءَآللَّهُ")], _variant("istifham_article", "ibdal", [0])),
    ),
    # Joined on into the next verse: the wasl selector is present.
    36: _shard(
        _reading(
            [("36:1:1", "يسٓ"), ("36:2:1", "وَٱلْقُرْءَانِ")], _variant("yaseen_wasl", "izhar", [0, 1])
        )
    ),
    # A reading that did not start at al-ism carries no alism_ibtidaa variant: no row.
    49: _shard(_reading([("49:11:30", "ٱلِٱسْمُ")])),
    69: _shard(_reading([("26:63:11", "فِرْقٍ")], _variant("raa_firq", "light", [0]))),
}


@pytest.fixture
def hafs(monkeypatch):
    monkeypatch.setattr(readings, "is_hafs", lambda slug: True)
    monkeypatch.setattr(
        readings.data_dir,
        "read_timestamps_chapter",
        lambda slug, ch: json.dumps(SHARDS[ch]).encode() if ch in SHARDS else None,
    )
    readings.invalidate()


def _rows(doc):
    return {row.key: row for row in doc.rows}


def _options(row):
    return {o.option: [v.label for v in o.verses] for o in row.options}


def test_istifham_splits_per_word_in_mushaf_order(hafs):
    doc = readings.build("r")
    keys = [row.key for row in doc.rows if row.selector == "istifham_article"]
    assert keys == [
        "istifham_article/aldhakarayn",
        "istifham_article/alaan",
        "istifham_article/allah",
    ]
    assert _rows(doc)["istifham_article/alaan"].texts == ["ءَآلْـَٔـٰنَ"]


def test_both_options_marked_when_occurrences_differ(hafs):
    rows = _rows(readings.build("r"))
    assert _options(rows["istifham_article/allah"]) == {"ibdal": ["27:59"], "tashil": ["10:59"]}
    assert _options(rows["istifham_article/alaan"]) == {"ibdal": [], "tashil": ["10:51", "10:91"]}


def test_conditional_rows_only_where_they_applied(hafs):
    rows = _rows(readings.build("r"))
    assert _options(rows["yaa_aatani_waqf"]) == {"ithbat": [], "hadhf": ["27:36"]}
    assert _options(rows["yaseen_wasl"]) == {"izhar": ["36:1–2"], "idgham": []}
    assert "alism_ibtidaa" not in rows


def test_boundary_rows_keep_both_words_and_dedupe_repeats(hafs):
    row = _rows(readings.build("r"))["iwaja_qayyima"]
    assert row.texts == ["عِوَجَاۜ", "قَيِّمًا"]
    assert _options(row) == {"sakt": ["18:1–2"], "idraj": []}


def test_default_picks_and_hidden_selectors_are_dropped(hafs):
    selectors = {row.selector for row in readings.build("r").rows}
    assert "yabsut" not in selectors
    assert "raa_firq" not in selectors


def test_pre_v15_shards_contribute_nothing():
    assert (
        readings.shard_hits(
            _shard(
                _reading([("6:143:10", "x")], _variant("istifham_article", "ibdal", [0])),
                version=14,
            )
        )
        == []
    )


def test_non_hafs_is_empty(monkeypatch):
    monkeypatch.setattr(readings, "is_hafs", lambda slug: False)
    monkeypatch.setattr(
        readings.data_dir, "read_timestamps_chapter", lambda *a: pytest.fail("read a shard")
    )
    readings.invalidate()
    assert readings.build("warsh").rows == []
    assert readings.doc("warsh").rows == []


def test_doc_builds_and_stores_on_first_request(hafs, monkeypatch):
    from services.storage.hf_bucket import StorageNotFound

    written = {}

    class Backend:
        def read_json(self, path):
            if path not in written:
                raise StorageNotFound(path)
            return written[path]

        def write_json_atomic(self, path, obj):
            written[path] = obj

    monkeypatch.setattr(readings, "get_backend", lambda: Backend())
    first = readings.doc("r")
    assert written["reciters/r/readings.json"]["rows"]
    readings.invalidate()
    assert readings.doc("r") == first


def _compact(*readings_):
    """A shard serialized like the builders write it: compact, pydantic field order."""
    doc = {
        "_meta": {"schema_version": 15, "variant_catalogue": CATALOGUE},
        "readings": [
            {"id": f"r{i}", "parts": [], "render": r["render"], "timing": {"w": []}}
            | ({"variants": r["variants"]} if r["variants"] else {})
            for i, r in enumerate(readings_)
        ],
    }
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()


def test_variant_view_decodes_only_readings_with_variants():
    body = _compact(
        _reading([("10:50:1", "قُلْ")]),
        _reading([("10:51:7", "ءَآلْـَٔـٰنَ")], _variant("istifham_article", "tashil", [0])),
        _reading([("10:52:1", "ثُمَّ")]),
        _reading([("10:91:1", "ءَآلْـَٔـٰنَ")], _variant("istifham_article", "tashil", [0])),
    )
    view = readings.variant_view(body)
    assert [r["id"] for r in view["readings"]] == ["r1", "r3"]
    assert readings.shard_hits(view) == readings.shard_hits(json.loads(body))


def test_variant_view_reads_any_other_layout_whole():
    body = json.dumps(SHARDS[10]).encode()
    assert readings.variant_view(body) == SHARDS[10]


def test_concurrent_first_requests_build_once(hafs, monkeypatch):
    import threading
    import time

    from services.storage.hf_bucket import StorageNotFound

    builds = []

    class Backend:
        def read_json(self, path):
            raise StorageNotFound(path)

        def write_json_atomic(self, path, obj):
            pass

    def slow_build(slug):
        builds.append(slug)
        time.sleep(0.2)
        return readings.TsReadingsDoc(slug=slug)

    monkeypatch.setattr(readings, "get_backend", lambda: Backend())
    monkeypatch.setattr(readings, "build", slow_build)
    threads = [threading.Thread(target=readings.doc, args=("r",)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert builds == ["r"]


def test_verse_read_twice_jumps_to_the_rendition_that_read_the_option():
    stopped = {"parts": [["68:1", 14570, 17780, 0, 1]], "render": {"w": [["68:1:1", "نٓ", []]]}}
    joined = {
        "parts": [["68:1", 24245, 32995, 0, 5]],
        "render": {"w": [[f"68:1:{i}", "w", []] for i in range(1, 6)]},
        "variants": [_variant("noon_wasl", "izhar", [0])],
    }
    shard = _shard(stopped, joined)
    shard["_meta"]["variant_catalogue"]["noon_wasl"] = {"options": ["izhar", "idgham"]}
    (row,) = readings.fold(readings.shard_hits(shard))
    (verse,) = row.options[0].verses
    assert (verse.label, verse.start_ms) == ("68:1", 24245)


def test_older_stored_summary_is_rebuilt(hafs, monkeypatch):
    from services.storage.hf_bucket import StorageNotFound

    stored = {"reciters/r/readings.json": {"schema_version": 1, "slug": "r", "rows": []}}

    class Backend:
        def read_json(self, path):
            if path not in stored:
                raise StorageNotFound(path)
            return stored[path]

        def write_json_atomic(self, path, obj):
            stored[path] = obj

    monkeypatch.setattr(readings, "get_backend", lambda: Backend())
    assert readings.doc("r").rows
    assert stored["reciters/r/readings.json"]["schema_version"] == readings.READINGS_SCHEMA_VERSION
