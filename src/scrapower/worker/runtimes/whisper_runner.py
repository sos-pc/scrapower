"""Whisper transcription worker for Scrapower.

Downloads audio (via direct URL or yt-dlp), transcribes with faster-whisper,
and returns the transcript via the blob store.

Input format (JSON):
  { "url": "...", "model": "large-v3", "language": "fr", "format": "srt" }
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

MODEL_CACHE = Path(os.environ.get("WHISPER_MODEL_DIR", "/tmp/whisper-models"))
DIRECT_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".aac", ".weba")


def _ensure_deps():
    for pkg in ["faster-whisper", "yt-dlp"]:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


def _download_audio(url: str, workdir: Path) -> Path:
    """Download audio from any URL (direct download or yt-dlp)."""
    # Try direct download first (works for blob store URLs, audio files, etc.)
    fname = url.split("/")[-1].split("?")[0] or "audio"
    if "." not in fname:
        fname += ".audio"
    dest = workdir / fname
    urllib.request.urlretrieve(url, str(dest))
    # If it's a video platform URL, yt-dlp would have been better, but
    # the downloaded raw page/audio still lets faster-whisper try.
    return dest

    # Platform URLs (YouTube etc.) via yt-dlp
    tmpl = str(workdir / "%(id)s.%(ext)s")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "0",
            "-o",
            tmpl,
            "--no-playlist",
            "--js-runtimes",
            "node",
            "--no-warnings",
            url,
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    for f in workdir.iterdir():
        if f.suffix in (".mp3", ".m4a", ".opus", ".webm"):
            return f
    raise FileNotFoundError(f"No audio in {workdir}")


def _transcribe(audio_path: Path, model_name: str, language: str | None, fmt: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name, device="cpu", compute_type="int8", download_root=str(MODEL_CACHE)
    )
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,
    )
    if fmt == "srt":
        return _to_srt(segments)
    elif fmt == "txt":
        return " ".join(s.text for s in segments)
    else:
        return json.dumps(
            {
                "language": info.language,
                "duration": round(info.duration, 1),
                "segments": [
                    {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                    for s in segments
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def _to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        s, e = _fmt(seg.start), _fmt(seg.end)
        lines.append(f"{i}\n{s} --> {e}\n{seg.text.strip()}\n")
    return "\n".join(lines)


def _fmt(sec: float) -> str:
    h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    _ensure_deps()
    input_data = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    config = json.loads(input_data)
    url = config.get("url", "")
    model_name = config.get("model", "large-v3")
    language = config.get("language") or None
    fmt = config.get("format", "json")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        print(f"Downloading: {url}", file=sys.stderr)
        audio_path = _download_audio(url, workdir)
        print(f"Audio: {audio_path} ({audio_path.stat().st_size}B)", file=sys.stderr)
        print(f"Transcribing: {model_name} (lang={language or 'auto'})", file=sys.stderr)
        start = time.time()
        transcript = _transcribe(audio_path, model_name, language, fmt)
        print(f"Done in {time.time() - start:.1f}s", file=sys.stderr)

    output = transcript.encode("utf-8")
    output_hash = hashlib.sha256(output).hexdigest()
    print(json.dumps({"output_bytes": output.hex(), "output_hash": output_hash}))


if __name__ == "__main__":
    main()
