"""A broken stream must not burn a whole task attempt.

Bilibili served 161 bytes of a 115MB audio stream and yt-dlp gave up after its
ten internal retries -- twice, hours apart, on different workers, always the
same byte count. Retrying the *same* format could never work; another rendition
of the same video downloaded fine. So a truncated download now falls back to a
different audio format, and only then gives up.
"""

from __future__ import annotations

import json
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


# ── Truncation guard ───────────────────────────────────────────────────────


def _advertise(tmp_path, seconds):
    (tmp_path / "vid.info.json").write_text(json.dumps({"duration": seconds}), encoding="utf-8")
    (tmp_path / "vid.m4a").write_bytes(b"audio")


def test_a_truncated_download_is_rejected():
    """The bug this exists for: 54s decoded from a 7347s lecture, delivered."""
    with pytest.raises(wr.DownloadError, match="truncated download"):
        wr._verify_duration(54.4, 7347.0)


def test_a_complete_download_passes():
    wr._verify_duration(7345.0, 7347.0)


def test_small_shortfalls_are_tolerated():
    """Decoded length and advertised length rarely agree to the second."""
    wr._verify_duration(1000 * wr.MIN_DURATION_RATIO + 1, 1000)


@pytest.mark.parametrize(
    ("actual", "expected", "reason"),
    [
        (12.0, 0.0, "site advertised nothing"),
        (0.0, 7347.0, "decoder reported nothing"),
    ],
)
def test_no_opinion_without_both_numbers(actual, expected, reason):
    """Never fail a task just because a reference is missing."""
    wr._verify_duration(actual, expected)


# ── Advertised duration ────────────────────────────────────────────────────


def test_the_advertised_duration_is_read(tmp_path):
    _advertise(tmp_path, 7347)
    assert wr._expected_duration(tmp_path) == 7347.0


@pytest.mark.parametrize(
    ("info", "reason"),
    [
        (None, "no info.json written"),
        ("{bad json", "unparseable info.json"),
        ('{"duration": null}', "site advertised no duration"),
        ('{"duration": "unknown"}', "non-numeric duration"),
        ('["not", "an", "object"]', "info.json root is not a mapping"),
        ('{"duration": {"nested": 1}}', "duration is not a scalar"),
    ],
)
def test_an_unusable_info_json_yields_no_reference(tmp_path, info, reason):
    if info is not None:
        (tmp_path / "vid.info.json").write_text(info, encoding="utf-8")
    assert wr._expected_duration(tmp_path) == 0.0


def test_the_info_json_is_requested(runs, tmp_path):
    calls, _ = runs
    (tmp_path / "vid.m4a").write_bytes(b"audio")
    wr._download_audio("https://youtu.be/x", tmp_path)
    assert "--write-info-json" in calls[0], "without it there is nothing to compare against"


def test_transcribe_rejects_a_short_decode(monkeypatch, tmp_path):
    """Wiring: the check must sit on the path that produces the transcript."""

    class Seg:
        start, end, text = 0.0, 54.4, " fragment"

    monkeypatch.setattr(
        wr, "_transcribe_faster_whisper", lambda *a, **kw: ([Seg()], "zh", 54.4, "cuda")
    )
    with pytest.raises(wr.DownloadError, match="truncated download"):
        wr._transcribe(tmp_path / "vid.m4a", "large-v3", "zh", "json", expected_duration=7347.0)


def test_transcribe_returns_normally_without_an_expected_duration(monkeypatch, tmp_path):
    """A coordinator-supplied audio blob has no advertised duration to check."""

    class Seg:
        start, end, text = 0.0, 54.4, " fragment"

    monkeypatch.setattr(
        wr, "_transcribe_faster_whisper", lambda *a, **kw: ([Seg()], "zh", 54.4, "cuda")
    )
    out = wr._transcribe(tmp_path / "vid.m4a", "large-v3", "zh", "json")
    assert "fragment" in out


# ── main(): the wiring that actually runs in production ────────────────────


class _Seg:
    start, end, text = 0.0, 54.4, " fragment"


@pytest.fixture
def fake_worker(monkeypatch, tmp_path):
    """Run main() without network, pip or a GPU.

    Covers the one line that matters most: main() has to hand the advertised
    duration to _transcribe. Nothing else notices if it stops doing so.
    """

    def setup(advertised, decoded):
        monkeypatch.setattr(wr, "_ensure_deps", lambda: None)

        def fake_download(url, workdir, cookies_path=None):
            (workdir / "vid.info.json").write_text(
                json.dumps({"duration": advertised}), encoding="utf-8"
            )
            audio = workdir / "vid.m4a"
            audio.write_bytes(b"audio")
            return audio

        monkeypatch.setattr(wr, "_download_audio", fake_download)
        monkeypatch.setattr(
            wr,
            "_transcribe_faster_whisper",
            lambda *a, **kw: ([_Seg()], "zh", decoded, "cuda"),
        )
        monkeypatch.setattr(
            wr.sys, "argv", ["whisper_runner", json.dumps({"url": "https://x/v", "format": "json"})]
        )

    return setup


def _result(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_main_reports_a_truncated_download_as_a_download_error(fake_worker, capsys):
    """exit_code 2 is what tells the coordinator to retry the download."""
    fake_worker(advertised=7347, decoded=54.4)
    wr.main()

    out = _result(capsys)
    assert out["exit_code"] == 2, "a fragment must not come back as a success"
    assert "truncated download" in bytes.fromhex(out["output_bytes"]).decode()


def test_main_succeeds_on_a_complete_download(fake_worker, capsys):
    fake_worker(advertised=7347, decoded=7345.0)
    wr.main()

    out = _result(capsys)
    assert out["exit_code"] == 0
    assert "fragment" in bytes.fromhex(out["output_bytes"]).decode()


def test_with_format_replaces_only_the_format(runs):
    argv = ["python", "-m", "yt_dlp", "-f", "bestaudio/best", "-o", "t", "--no-playlist", "URL"]
    out = wr._with_format(argv, "worstaudio")
    assert out[4] == "worstaudio"
    assert out[:4] == argv[:4] and out[5:] == argv[5:], "nothing else may move"
    assert argv[4] == "bestaudio/best", "the original argv must not be mutated"
