from qua_shared.audio import upstream

ENTRY = {
    "url": "https://cdn/62.mp3",
    "size_bytes": 7_437_367,
    "duration_sec": 465,
    "bitrate_kbps": 128,
}


def test_same_size_is_ok():
    assert upstream.classify(ENTRY, {"size": 7_437_367, "duration_s": 300.0}) == "ok"


def test_different_duration_is_a_blocking_recording_change():
    verdict = upstream.classify(ENTRY, {"size": 6_424_236, "duration_s": 321.1, "kbps": 160})
    assert verdict == "recording"
    assert verdict in upstream.BLOCKING


def test_same_duration_other_bitrate_is_a_reencode():
    assert (
        upstream.classify(ENTRY, {"size": 1_000_000, "duration_s": 465.4, "kbps": 32}) == "reencode"
    )


def test_small_size_change_is_a_tag_edit():
    assert (
        upstream.classify(ENTRY, {"size": 7_437_378, "duration_s": 465.0, "kbps": 128})
        == "tag_edit"
    )


def test_bucket_format_stands_in_for_a_manifest_without_bitrate():
    entry = {"url": "https://cdn/1.mp3", "size_bytes": 1_330_551, "duration_sec": 55}
    up = {"size": 258_101, "duration_s": 55.0, "kbps": 32, "sr": 11025}
    assert upstream.classify(entry, up, held={"kbps": 192, "sr": 44100}) == "reencode"


def test_gone_and_errors_never_block():
    assert upstream.classify(ENTRY, {"gone": 404}) == "gone"
    assert upstream.classify(ENTRY, {"error": "timeout"}) == "error"
    assert not upstream.BLOCKING & {"gone", "error"}


def test_skipped_sources_are_not_probed(monkeypatch):
    seen = []
    monkeypatch.setattr(upstream, "probe", lambda url: seen.append(url) or {"size": 1})
    chapters = {
        "1": {"url": "https://www.youtube.com/watch?v=x"},
        "2": {"url": "https://cdn/2.mp3", "source_url": "https://drive.google.com/file/d/x"},
        "3": {"url": "/local/3.mp3"},
        "4": {"url": "https://cdn/4.mp3", "size_bytes": 1},
        "1:1": {"url": "https://cdn/1_1.mp3"},
    }
    checks = upstream.check_chapters(chapters)
    assert seen == ["https://cdn/4.mp3"]
    assert [c.verdict for c in checks] == ["ok"]


def test_blocking_changes_describe_the_change(monkeypatch):
    monkeypatch.setattr(upstream, "probe", lambda url: {"size": 6_424_236, "duration_s": 321.13})
    (change,) = upstream.blocking_changes({"62": ENTRY})
    assert change.describe() == "ch62 recording (465s → 321s)"
