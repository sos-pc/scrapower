"""Whisper transcription worker for Scrapower."""

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


def _download_audio(url, workdir, cookies_path=None):
    is_direct = any(url.lower().endswith(e) for e in DIRECT_EXTS) or "/blobs/" in url
    if is_direct:
        fname = url.split("/")[-1].split("?")[0] or "audio"
        if "." not in fname:
            fname += ".audio"
        dest = workdir / fname
        urllib.request.urlretrieve(url, str(dest))
        return dest
    tmpl = str(workdir / "%(id)s.%(ext)s")
    args = [
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
        "--no-warnings",
    ]
    if cookies_path:
        args += ["--cookies", cookies_path]
    args.append(url)
    subprocess.run(args, check=True, capture_output=True, timeout=600)
    for f in workdir.iterdir():
        if f.suffix in (".mp3", ".m4a", ".opus", ".webm"):
            return f
    raise FileNotFoundError(f"No audio in {workdir}")


def _transcribe(audio_path, model_name, language, fmt):
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_name, device="cuda", compute_type="float16", download_root=str(MODEL_CACHE)
    )
    segments, info = model.transcribe(
        str(audio_path), language=language, beam_size=5, vad_filter=True
    )
    if fmt == "srt":
        lines = []
        for i, seg in enumerate(segments, 1):
            s, e = _fmt(seg.start), _fmt(seg.end)
            lines.append(f"{i}\n{s} --> {e}\n{seg.text.strip()}\n")
        return "\n".join(lines)
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


def _fmt(sec):
    h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    try:
        _ensure_deps()
        config = json.loads(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())
        url = config.get("url", "")
        audio_hash = config.get("audio_hash", "")
        coordinator_url = config.get("coordinator_url", "https://scrapower.talos-int.com")
        model_name = config.get("model", "large-v3")
        language = config.get("language") or None
        fmt = config.get("format", "json")
        cookies_hash = config.get("cookies_hash", "")
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            cookies_path = None
            if cookies_hash:
                cookies_path = str(workdir / "cookies.txt")
                urllib.request.urlretrieve(f"{coordinator_url}/blobs/{cookies_hash}", cookies_path)
            # Mode B: receive audio as blob (no internet needed on worker)
            if audio_hash:
                audio_path = workdir / "audio.mp3"
                print(f"Downloading audio blob: {audio_hash[:12]}...", file=sys.stderr)
                urllib.request.urlretrieve(f"{coordinator_url}/blobs/{audio_hash}", str(audio_path))
            elif url:
                # Legacy: direct URL download (requires internet on worker)
                print(f"Downloading: {url}", file=sys.stderr)
                audio_path = _download_audio(url, workdir, cookies_path)
            else:
                raise ValueError("Neither audio_hash nor url provided")
            print(f"Transcribing: {model_name}", file=sys.stderr)
            start = time.time()
            transcript = _transcribe(audio_path, model_name, language, fmt)
            print(f"Done in {time.time() - start:.1f}s", file=sys.stderr)
        output = transcript.encode("utf-8")
        output_hash = hashlib.sha256(output).hexdigest()
        print(json.dumps({"output_bytes": output.hex(), "output_hash": output_hash}))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(err, file=sys.stderr)
        output = err.encode("utf-8")
        output_hash = hashlib.sha256(output).hexdigest()
        print(
            json.dumps({"output_bytes": output.hex(), "output_hash": output_hash, "exit_code": 1})
        )


if __name__ == "__main__":
    main()
