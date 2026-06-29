"""Whisper transcription worker for Scrapower.

Backends:
  - faster-whisper (ctranslate2) — primary, used on Modal / HF Spaces
  - transformers (PyTorch native) — fallback for Kaggle (ctranslate2 GPU incompatible)
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


class DownloadError(Exception):
    """Audio download failed — signals coordinator to prepare fallback."""


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
        "-f",
        "bestaudio/best",
        "-o",
        tmpl,
        "--no-playlist",
        "--no-warnings",
    ]
    wg_proxy = os.environ.get("WG_PROXY", "")
    if wg_proxy:
        args += ["--proxy", wg_proxy]
        cookies_path = None
        print("[whisper_runner] using WireGuard proxy, cookies disabled", file=sys.stderr)
    else:
        print("[whisper_runner] no proxy configured", file=sys.stderr)
    if cookies_path:
        args += ["--cookies", cookies_path]
    args.append(url)
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode()[-500:] if e.stderr else ""
        if "Requested format is not available" in stderr and cookies_path:
            args_no_cookies = [a for a in args if a != "--cookies" and a != cookies_path]
            try:
                subprocess.run(args_no_cookies, check=True, capture_output=True, timeout=600)
            except subprocess.CalledProcessError as e2:
                raise DownloadError(
                    f"yt-dlp failed (rc={e2.returncode}): {e2.stderr.decode()[-500:] if e2.stderr else 'no stderr'}"
                )
        else:
            raise DownloadError(f"yt-dlp failed (rc={e.returncode}): {stderr}")
    for f in workdir.iterdir():
        if f.suffix in (".m4a", ".opus", ".webm", ".mp3"):
            return f
    raise FileNotFoundError(f"No audio in {workdir}")


# ---------------------------------------------------------------------------
#  Formatting helpers (shared between backends)
# ---------------------------------------------------------------------------


def _fmt(sec):
    h, m, s = int(sec // 3600), int((sec % 3600) // 60), int(sec % 60)
    ms = int((sec - int(sec)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_segments(seg_list, language, duration, fmt):
    """Render a list of segment-like objects to the requested format."""
    if fmt == "srt":
        lines = []
        for i, seg in enumerate(seg_list, 1):
            s, e = _fmt(seg.start), _fmt(seg.end)
            lines.append(f"{i}\n{s} --> {e}\n{seg.text.strip()}\n")
        return "\n".join(lines)
    elif fmt == "txt":
        return " ".join(s.text.strip() for s in seg_list)
    else:
        return json.dumps(
            {
                "language": language,
                "duration": round(duration, 1),
                "segments": [
                    {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                    for s in seg_list
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


# ---------------------------------------------------------------------------
#  Backend: faster-whisper (ctranslate2) — primary
# ---------------------------------------------------------------------------

# -- Backend: faster-whisper (ctranslate2) --------------------------


def _transcribe_faster_whisper(audio_path, model_name, language):
    """Return (seg_list, language_str, duration_sec, device_used) or None."""
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    for device, compute_type in (("cuda", "float16"), ("cpu", "int8")):
        try:
            model = WhisperModel(
                model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(MODEL_CACHE),
            )
            print(
                f"[whisper_runner] faster-whisper loaded on {device} ({compute_type})",
                file=sys.stderr,
            )
            break
        except RuntimeError as e:
            print(
                f"[whisper_runner] faster-whisper {device} failed: {e}",
                file=sys.stderr,
            )
    else:
        return None

    batched = BatchedInferencePipeline(model=model)
    segments, info = batched.transcribe(
        str(audio_path), language=language, batch_size=8, beam_size=5, vad_filter=True
    )
    seg_list = []
    last_log = time.time()
    for i, seg in enumerate(segments):
        seg_list.append(seg)
        if time.time() - last_log > 30:
            print(
                f"  ... transcribed {i + 1} segments ({seg.start:.0f}s)",
                file=sys.stderr,
            )
            last_log = time.time()
    return seg_list, info.language, info.duration, device


# -- Orchestrator -------------------------------------------------------


def _transcribe(audio_path, model_name, language, fmt):
    """Transcribe audio. Tries CUDA first, falls back to CPU."""
    result = _transcribe_faster_whisper(audio_path, model_name, language)
    if result is None:
        raise RuntimeError("No viable device for WhisperModel")
    seg_list, lang, dur, _ = result
    return _format_segments(seg_list, lang, dur, fmt)


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------


def main():
    print("whisper_runner: starting", file=sys.stderr)
    try:
        _ensure_deps()
        config = json.loads(sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read())
        url = config.get("url", "")
        audio_hash = config.get("audio_hash", "")
        coordinator_url = config.get("coordinator_url") or os.environ.get(
            "SCRAPOWER_COORDINATOR_URL", "http://localhost:8777"
        )
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
            if audio_hash:
                audio_path = workdir / "audio.mp3"
                print(f"Downloading audio blob: {audio_hash[:12]}...", file=sys.stderr)
                urllib.request.urlretrieve(f"{coordinator_url}/blobs/{audio_hash}", str(audio_path))
            elif url:
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
        print(
            json.dumps({"output_bytes": output.hex(), "output_hash": output_hash, "exit_code": 0})
        )
    except DownloadError as e:
        err = f"DownloadError: {e}"
        print(err, file=sys.stderr)
        output = err.encode("utf-8")
        output_hash = hashlib.sha256(output).hexdigest()
        print(
            json.dumps({"output_bytes": output.hex(), "output_hash": output_hash, "exit_code": 2})
        )
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
