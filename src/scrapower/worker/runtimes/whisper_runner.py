"""Whisper transcription worker for Scrapower.

Downloads audio (via URL or blob hash), transcribes with faster-whisper,
and returns the transcript via the blob store.

Input format (JSON):
  {
    "url": "https://youtube.com/watch?v=...",   // OR input_hash from blob store
    "model": "large-v3",                         // tiny, base, small, medium, large-v3
    "language": "fr",                            // auto-detect if omitted
    "format": "srt"                              // srt, txt, json (default: json)
  }

Dependencies needed on the worker:
  - faster-whisper
  - yt-dlp (for URL downloads)
  - ffmpeg (for audio extraction fallback and yt-dlp)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── Cache directories ──────────────────────────────────────────
MODEL_CACHE = Path(os.environ.get("WHISPER_MODEL_DIR", "/tmp/whisper-models"))


def _ensure_deps():
    """Install missing packages (idempotent, runs on worker startup)."""
    deps = ["faster-whisper", "yt-dlp"]
    for pkg in deps:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


def _download_audio(url: str, workdir: Path) -> Path:
    """Download audio-only from a URL using yt-dlp.

    Returns path to the audio file (mp3 or m4a).
    """
    output_template = str(workdir / "%(id)s.%(ext)s")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "-x",  # extract audio
            "--audio-format",
            "mp3",  # prefer mp3
            "--audio-quality",
            "0",  # best quality
            "-o",
            output_template,
            "--no-playlist",
            "--no-warnings",
            url,
        ],
        check=True,
        capture_output=True,
        timeout=600,  # 10 min max for download
    )
    # Find the downloaded audio file
    for f in workdir.iterdir():
        if f.suffix in (".mp3", ".m4a", ".opus", ".webm"):
            return f
    raise FileNotFoundError(f"No audio file found in {workdir}")


def _transcribe(audio_path: Path, model_name: str, language: str | None, fmt: str) -> str:
    """Transcribe audio file using faster-whisper.

    Returns the transcript in the requested format.
    """
    from faster_whisper import WhisperModel

    # Load model (cached on disk after first download)
    model = WhisperModel(
        model_name, device="cpu", compute_type="int8", download_root=str(MODEL_CACHE)
    )

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        vad_filter=True,  # skip silence
    )

    if fmt == "srt":
        return _to_srt(segments)
    elif fmt == "txt":
        return " ".join(seg.text for seg in segments)
    else:  # json
        segs = [
            {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text.strip()}
            for seg in segments
        ]
        return json.dumps(
            {
                "language": info.language,
                "duration": round(info.duration, 1),
                "segments": segs,
            },
            ensure_ascii=False,
            indent=2,
        )


def _to_srt(segments) -> str:
    """Convert segments to SRT subtitle format."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _fmt_time(seg.start)
        end = _fmt_time(seg.end)
        text = seg.text.strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── Main entry point ───────────────────────────────────────────


def main():
    """Called by the worker sandbox: receives JSON input via stdin or argv."""
    _ensure_deps()

    # Read input
    if len(sys.argv) > 1:
        input_data = sys.argv[1]
    else:
        input_data = sys.stdin.read()

    config = json.loads(input_data)
    url = config.get("url", "")
    model_name = config.get("model", "large-v3")
    language = config.get("language") or None  # None = auto-detect
    fmt = config.get("format", "json")

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        if url:
            # Download audio from URL
            print(f"Downloading audio from: {url}", file=sys.stderr)
            audio_path = _download_audio(url, workdir)
        else:
            # Assume audio file was passed as bytes (from blob store)
            # Not implemented in this version — use URL mode
            raise ValueError("URL required (input_hash mode not yet implemented)")

        print(f"Audio: {audio_path} ({audio_path.stat().st_size} bytes)", file=sys.stderr)
        print(f"Transcribing with model: {model_name} (lang={language or 'auto'})", file=sys.stderr)

        start = time.time()
        transcript = _transcribe(audio_path, model_name, language, fmt)
        elapsed = time.time() - start

        print(f"Done in {elapsed:.1f}s", file=sys.stderr)

    # Output transcript as bytes (Scrapower sandbox expects bytes output)
    output = transcript.encode("utf-8")
    output_hash = hashlib.sha256(output).hexdigest()
    print(json.dumps({"output_bytes": output.hex(), "output_hash": output_hash}))


if __name__ == "__main__":
    main()
