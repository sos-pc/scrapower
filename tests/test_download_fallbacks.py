"""A broken stream must not burn a whole task attempt.

Bilibili served 161 bytes of a 115MB audio stream and yt-dlp gave up after its
ten internal retries -- twice, hours apart, on different workers, always the
same byte count. Retrying the *same* format could never work; another rendition
of the same video downloaded fine. So a truncated download now falls back to a
different audio format, and only then gives up.
"""

from __future__ import annotations

import subprocess

import pytest

from scrapower.worker.runtimes import whisper_runner as wr

TRUNCATED = (
    "ERROR: [download] Got error: 161 bytes read, 115871193 more expected."
    " Giving up after 10 retries"
)
NO_FORMAT = "ERROR: Requested format is not available"


@pytest.fixture
def runs(monkeypatch, tmp_path):
    """Record every yt-dlp argv; the outcome is scripted per call."""
    calls: list[list[str]] = []
    script: list[tuple[bool, int, str]] = []

    def fake_run(args, timeout=1800):
        calls.append(list(args))
        return script[len(calls) - 1] if len(calls) <= len(script) else (True, 0, "")

    monkeypatch.setattr(wr, "_run_ytdlp", fake_run)
    return calls, script


def _fmt_of(argv):
    return argv[argv.index("-f") + 1]


def test_a_truncated_stream_retries_another_format(runs, tmp_path, capsys):
    calls, script = runs
    script.append((False, 1, TRUNCATED))  # first attempt: broken stream
    script.append((True, 0, ""))  # fallback format works
    (tmp_path / "vid.m4a").write_bytes(b"audio")

    got = wr._download_audio("https://www.bilibili.com/video/BV1x/?p=9", tmp_path)

    assert got.name == "vid.m4a"
    assert len(calls) == 2, "must retry rather than give up"
    assert _fmt_of(calls[0]) == "bestaudio/best"
    assert _fmt_of(calls[1]) == wr.FALLBACK_FORMAT
    assert "truncated" in capsys.readouterr().err


def test_a_successful_download_does_not_retry(runs, tmp_path):
    calls, _ = runs
    (tmp_path / "vid.m4a").write_bytes(b"audio")

    wr._download_audio("https://example.com/watch?v=x", tmp_path)

    assert len(calls) == 1, "the happy path must stay one invocation"
    assert _fmt_of(calls[0]) == "bestaudio/best"


def test_other_errors_are_not_retried_with_a_new_format(runs, tmp_path):
    """A private or geo-blocked video is not a stream problem -- fail fast."""
    calls, script = runs
    script.append((False, 1, "ERROR: Private video. Sign in if you've been granted access"))

    with pytest.raises(wr.DownloadError, match="Private video"):
        wr._download_audio("https://youtu.be/x", tmp_path)

    assert len(calls) == 1, "no point re-downloading a video we cannot access"


def test_the_cookie_fallback_still_works(runs, tmp_path, monkeypatch):
    """Pre-existing behaviour: cookies can narrow the format list."""
    monkeypatch.delenv("WG_PROXY", raising=False)
    calls, script = runs
    script.append((False, 1, NO_FORMAT))
    script.append((True, 0, ""))
    (tmp_path / "vid.m4a").write_bytes(b"audio")

    wr._download_audio("https://youtu.be/x", tmp_path, cookies_path="/tmp/c.txt")

    assert len(calls) == 2
    assert "--cookies" in calls[0]
    assert "--cookies" not in calls[1], "the retry must be anonymous"
    assert _fmt_of(calls[1]) == "bestaudio/best", "cookie retry keeps the format"


def test_both_fallbacks_can_chain(runs, tmp_path, monkeypatch):
    monkeypatch.delenv("WG_PROXY", raising=False)
    calls, script = runs
    script.append((False, 1, NO_FORMAT))
    script.append((False, 1, TRUNCATED))
    script.append((True, 0, ""))
    (tmp_path / "vid.m4a").write_bytes(b"audio")

    wr._download_audio("https://youtu.be/x", tmp_path, cookies_path="/tmp/c.txt")

    assert len(calls) == 3
    assert "--cookies" not in calls[2], "the format retry keeps the anonymous argv"
    assert _fmt_of(calls[2]) == wr.FALLBACK_FORMAT


def test_a_failing_fallback_reports_the_real_error(runs, tmp_path):
    calls, script = runs
    script.append((False, 1, TRUNCATED))
    script.append((False, 1, "ERROR: nothing worked either"))

    with pytest.raises(wr.DownloadError, match="nothing worked either"):
        wr._download_audio("https://youtu.be/x", tmp_path)

    assert len(calls) == 2


# ── _run_ytdlp itself ──────────────────────────────────────────────────────


def test_a_timeout_becomes_a_download_error(monkeypatch, tmp_path):
    """Exit code 2 tells the coordinator to prepare a fallback; an unhandled
    TimeoutExpired would have surfaced as a generic exit 1 instead."""

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=1800)

    monkeypatch.setattr(wr.subprocess, "run", boom)
    ok, rc, err = wr._run_ytdlp(["yt-dlp"], timeout=1800)
    assert not ok
    assert "timed out" in err

    monkeypatch.setattr(wr, "_run_ytdlp", lambda args, timeout=1800: (False, -1, "timed out"))
    with pytest.raises(wr.DownloadError, match="timed out"):
        wr._download_audio("https://youtu.be/x", tmp_path)


def test_with_format_replaces_only_the_format(runs):
    argv = ["python", "-m", "yt_dlp", "-f", "bestaudio/best", "-o", "t", "--no-playlist", "URL"]
    out = wr._with_format(argv, "worstaudio")
    assert out[4] == "worstaudio"
    assert out[:4] == argv[:4] and out[5:] == argv[5:], "nothing else may move"
    assert argv[4] == "bestaudio/best", "the original argv must not be mutated"
