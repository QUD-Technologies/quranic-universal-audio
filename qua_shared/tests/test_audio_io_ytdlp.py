"""Fetch failure handling in the acquire job: the real yt-dlp reason survives, a
bot-check refusal stops the other YouTube fetches, and a cookies file whose
tabs became spaces is repaired, and a plain GET retries server errors."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from qua_jobs import acquire_audio, audio_io

_BOT = (
    "ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies-from-browser "
    "or --cookies for the authentication."
)
_ROTATED = "WARNING: [youtube] The provided YouTube account cookies are no longer valid."


def test_bot_check_with_rotated_cookies_names_the_rejection():
    with pytest.raises(audio_io.BotCheckError, match="cookies were rejected.*no longer valid"):
        audio_io._raise_ytdlp_failure(f"{_ROTATED}\n{_BOT}\n", had_cookies=True)


def test_bot_check_without_cookies_says_none_reached_the_job():
    with pytest.raises(audio_io.BotCheckError, match="no YTDLP_COOKIES secret"):
        audio_io._raise_ytdlp_failure(_BOT, had_cookies=False)


def test_other_failures_keep_the_ytdlp_error_line():
    out = "[youtube] abc: Downloading webpage\nERROR: [youtube] abc: Video unavailable\n"
    with pytest.raises(RuntimeError, match="Video unavailable") as info:
        audio_io._raise_ytdlp_failure(out, had_cookies=True)
    assert not isinstance(info.value, audio_io.BotCheckError)


def test_cookies_with_spaces_for_tabs_are_rebuilt_with_tabs():
    text = (
        "# Netscape HTTP Cookie File\n\n"
        ".youtube.com  TRUE  /  TRUE  1824879615  SID  g.a0\n"
        ".youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tx1\n"
    )
    rows = audio_io.normalize_cookies(text).splitlines()
    assert rows[0] == "# Netscape HTTP Cookie File"
    assert rows[2].split("\t") == [".youtube.com", "TRUE", "/", "TRUE", "1824879615", "SID", "g.a0"]
    assert rows[3] == ".youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tx1"


def test_after_a_bot_check_later_youtube_fetches_fail_without_a_request(monkeypatch):
    calls: list[str] = []

    def refuse(url, dest):
        calls.append(url)
        raise audio_io.BotCheckError("YouTube refused the download (bot check): x")

    monkeypatch.setattr(audio_io, "fetch", refuse)
    monkeypatch.setattr(acquire_audio, "_bot_blocked", [])
    for vid in ("a", "b", "c"):
        with pytest.raises(audio_io.BotCheckError):
            acquire_audio._fetch(f"https://www.youtube.com/watch?v={vid}", Path("x"))
    assert calls == ["https://www.youtube.com/watch?v=a"]


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://archive.org/x.mp3", code, "err", None, None)  # type: ignore[arg-type]


def test_http_get_retries_a_server_error_then_succeeds(monkeypatch, tmp_path):
    calls: list[int] = []

    def flaky(url, dest, *, refuse_html):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error(500)
        dest.write_bytes(b"ok")

    monkeypatch.setattr(audio_io, "_http_get_once", flaky)
    monkeypatch.setattr(audio_io, "HTTP_RETRY_SLEEP_S", 0)
    audio_io._http_get("https://archive.org/x.mp3", tmp_path / "a", refuse_html=False)
    assert len(calls) == 3
    assert (tmp_path / "a").read_bytes() == b"ok"


def test_http_get_fails_a_client_error_at_once(monkeypatch, tmp_path):
    calls: list[int] = []

    def gone(url, dest, *, refuse_html):
        calls.append(1)
        raise _http_error(404)

    monkeypatch.setattr(audio_io, "_http_get_once", gone)
    with pytest.raises(urllib.error.HTTPError):
        audio_io._http_get("https://archive.org/x.mp3", tmp_path / "a", refuse_html=False)
    assert len(calls) == 1
