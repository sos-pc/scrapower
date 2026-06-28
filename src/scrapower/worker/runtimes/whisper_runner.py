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
    # Ensure transformers + accelerate for PyTorch fallback (Kaggle GPU)
    try:
        __import__("transformers")
        __import__("accelerate")
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "transformers", "accelerate"]
        )


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


# ---------------------------------------------------------------------------
#  Backend: transformers (PyTorch native) — fallback for Kaggle
# ---------------------------------------------------------------------------

HF_MODEL_MAP = {
    "tiny": "openai/whisper-tiny",
    "tiny.en": "openai/whisper-tiny.en",
    "base": "openai/whisper-base",
    "small": "openai/whisper-small",
    "medium": "openai/whisper-medium",
    "large-v2": "openai/whisper-large-v2",
    "large-v3": "openai/whisper-large-v3",
    "turbo": "openai/whisper-large-v3-turbo",
}


def _transcribe_transformers(audio_path, model_name, language):
    """Return (seg_list, language_str, duration_sec). Uses PyTorch directly."""
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    hf_model = HF_MODEL_MAP.get(model_name, f"openai/whisper-{model_name}")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    print(
        f"[whisper_runner] Transformers: {hf_model} on {device} ({dtype})",
        file=sys.stderr,
    )

    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        hf_model,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    ).to(device)

    processor = AutoProcessor.from_pretrained(hf_model)

    pipe = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=dtype,
        device=device,
        chunk_length_s=30,
        batch_size=8 if device == "cuda" else 2,
        return_timestamps=True,
    )

    generate_kwargs: dict = {}
    if language:
        generate_kwargs["language"] = language

    print("[whisper_runner] Transformers transcribing...", file=sys.stderr)
    start = time.time()
    result = pipe(str(audio_path), generate_kwargs=generate_kwargs)
    elapsed = time.time() - start
    print(f"[whisper_runner] Transformers done in {elapsed:.1f}s", file=sys.stderr)

    chunks = result.get("chunks", [])
    detected_language = result.get("language", language or "unknown")
    duration = round(chunks[-1].get("timestamp", (0, 0))[1], 1) if chunks else 0

    # Wrap HF chunks into segment-like objects matching faster-whisper's API
    class _Segment:
        __slots__ = ("start", "end", "text")

        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    seg_list = [_Segment(c["timestamp"][0], c["timestamp"][1], c["text"]) for c in chunks]
    return seg_list, detected_language, duration


# ---------------------------------------------------------------------------
#  Orchestrator
# ---------------------------------------------------------------------------


def _transcribe(audio_path, model_name, language, fmt):
    """Transcribe audio with automatic backend selection.

    Strategy:
      1. Try faster-whisper (primary). Works on Modal (CUDA) and HF Spaces (CPU).
      2. If faster-whisper needed CPU but torch shows a GPU, we're on Kaggle
         where ctranslate2 is driver-incompatible. Use transformers GPU instead.
      3. If faster-whisper failed entirely, fall back to transformers.
    """
    import torch

    # 1. Try faster-whisper first
    result = _transcribe_faster_whisper(audio_path, model_name, language)
    if result is not None:
        seg_list, lang, dur, used_device = result
        # If torch says GPU is available but faster-whisper fell back to CPU,
        # we're on Kaggle — ctranslate2 is broken, transformers will be faster.
        if used_device == "cpu" and torch.cuda.is_available():
            print(
                "[whisper_runner] GPU available via torch but ctranslate2 failed, "
                "switching to transformers for GPU acceleration",
                file=sys.stderr,
            )
            seg_list, lang, dur = _transcribe_transformers(audio_path, model_name, language)
        return _format_segments(seg_list, lang, dur, fmt)

    # 2. faster-whisper failed entirely, fall back to transformers
    print(
        "[whisper_runner] faster-whisper unavailable, falling back to transformers",
        file=sys.stderr,
    )
    seg_list, lang, dur = _transcribe_transformers(audio_path, model_name, language)
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
