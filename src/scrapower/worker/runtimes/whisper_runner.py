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


# yt-dlp chases sites that actively change to break it, so a version that
# worked last month is not merely old, it is broken: workers running 2026.02.21
# got "HTTP 412 Precondition Failed" on every Bilibili download while the
# coordinator's 2026.07.04 succeeded through the same proxy in the same second.
# Ephemeral images cache their pip layer and `import yt_dlp` still succeeds, so
# install-if-missing pins the worker to whatever the image froze. Upgrade it
# every run; the heavy, stable deps stay install-if-missing.
ALWAYS_UPGRADE = ("yt-dlp",)
INSTALL_IF_MISSING = ("faster-whisper",)


def _ensure_deps():
    for pkg in INSTALL_IF_MISSING:
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])
    for pkg in ALWAYS_UPGRADE:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-U", pkg])
        except subprocess.CalledProcessError as e:
            # A transient index failure must not sink the task: whatever version
            # is already installed may still work. The import below is the real
            # gate, and it fails loudly if it does not.
            print(f"[whisper_runner] {pkg} upgrade failed (rc={e.returncode})", file=sys.stderr)
        mod = __import__(pkg.replace("-", "_"))
        # Log the version: when downloads start failing, this line is the
        # difference between minutes and hours of diagnosis.
        version = getattr(getattr(mod, "version", None), "__version__", None) or getattr(
            mod, "__version__", "?"
        )
        print(f"[whisper_runner] {pkg} {version}", file=sys.stderr)


# yt-dlp's wording when a stream dies mid-transfer, e.g.
# "Got error: 161 bytes read, 115871193 more expected. Giving up after 10 retries"
TRUNCATED_MARKERS = ("more expected", "Giving up after")

# Whisper resamples to 16 kHz mono, so the lowest audio-only rendition loses
# nothing that matters -- it is only a fallback because `bestaudio` is otherwise
# the safer pick on sites where "worst" can mean genuinely bad.
FALLBACK_FORMAT = "worstaudio/bestaudio/best"


def _run_ytdlp(args, timeout=1800):
    """Run yt-dlp. Returns (ok, returncode, stderr_tail)."""
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=timeout)
        return True, 0, ""
    except subprocess.CalledProcessError as e:
        return False, e.returncode, e.stderr.decode()[-500:] if e.stderr else ""
    except subprocess.TimeoutExpired:
        # Semantically a download failure, so it must reach the coordinator as
        # one (exit 2) rather than as an unhandled error.
        return False, -1, f"timed out after {timeout}s"


def _with_format(args, fmt):
    """Copy of args with the -f value replaced."""
    out = list(args)
    out[out.index("-f") + 1] = fmt
    return out


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
        # Records the advertised duration next to the audio, which is the only
        # way to notice that a "successful" download was actually truncated.
        "--write-info-json",
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
    ok, rc, stderr = _run_ytdlp(args)

    if not ok and "Requested format is not available" in stderr and cookies_path:
        # Cookies can narrow the advertised format list; retry anonymously.
        args = [a for a in args if a != "--cookies" and a != cookies_path]
        ok, rc, stderr = _run_ytdlp(args)

    if not ok and any(m in stderr for m in TRUNCATED_MARKERS):
        # The CDN served a broken stream for the format `bestaudio` chose:
        # Bilibili returned 161 bytes of a 115MB file on all ten of yt-dlp's
        # retries, twice, hours apart and from different workers, while another
        # rendition of the same video downloaded fine. Retrying the same format
        # is what wasted two full task attempts.
        print(
            "[whisper_runner] stream truncated, retrying a different audio format", file=sys.stderr
        )
        ok, rc, stderr = _run_ytdlp(_with_format(args, FALLBACK_FORMAT))

    if not ok:
        raise DownloadError(f"yt-dlp failed (rc={rc}): {stderr or 'no stderr'}")

    for f in workdir.iterdir():
        if f.suffix in (".m4a", ".opus", ".webm", ".mp3"):
            _verify_complete(f, workdir)
            return f
    raise FileNotFoundError(f"No audio in {workdir}")


# A truncated download is worse than a failed one: yt-dlp can exit 0 on a partial
# file, Whisper transcribes the fragment without complaining, and a 54-second
# transcript of a two-hour lecture gets delivered as if it were valid. That
# happened. So compare what landed against what the site advertised.
MIN_DURATION_RATIO = 0.9


def _audio_duration(path):
    """Seconds of decodable audio, or None if ffprobe is unavailable."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return float(out.stdout.decode().strip())
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"[whisper_runner] duration probe unavailable: {e}", file=sys.stderr)
        return None


def _verify_complete(audio_path, workdir):
    """Raise DownloadError if the audio is materially shorter than advertised."""
    info = next(workdir.glob("*.info.json"), None)
    if info is None:
        return  # nothing to compare against; not worth failing the task over
    try:
        expected = float(json.loads(info.read_text(encoding="utf-8")).get("duration") or 0)
    except (json.JSONDecodeError, OSError, TypeError, ValueError, AttributeError):
        # Anything unreadable, non-numeric, or not even an object: no reference,
        # so no opinion. This guard exists to catch bad downloads, not to invent
        # new ways for a good one to fail.
        return
    actual = _audio_duration(audio_path)
    if not expected or actual is None:
        return
    print(f"[whisper_runner] audio {actual:.0f}s of {expected:.0f}s advertised", file=sys.stderr)
    if actual < expected * MIN_DURATION_RATIO:
        raise DownloadError(
            f"truncated download: {actual:.0f}s of audio, expected {expected:.0f}s "
            f"({actual / expected:.0%})"
        )


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


def _transcribe_faster_whisper(
    audio_path, model_name, language, task="transcribe", initial_prompt="", hotwords=""
):
    """Return (seg_list, language_str, duration_sec, device_used) or None.

    ``language`` is the language *spoken in the audio* (a decoding hint), not an
    output language. ``task`` picks what comes out:
      - "transcribe": text in the spoken language
      - "translate":  speech translated to English (Whisper only ever targets
                      English; it cannot emit e.g. French directly, and `turbo`
                      is not trained for this task at all)
    """
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
    print(
        f"[whisper_runner] task={task} language={language or 'auto'}"
        f" hotwords={'yes' if hotwords else 'no'}"
        f" initial_prompt={'yes' if initial_prompt else 'no'}",
        file=sys.stderr,
    )
    kwargs = {}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    if hotwords:
        # Targeted at recurring proper nouns, which is where translation drifts
        # most (BRICS came back as "gold brick country", SCO as "Shanghe").
        kwargs["hotwords"] = hotwords
    segments, info = batched.transcribe(
        str(audio_path),
        language=language,
        task=task,
        batch_size=8,
        beam_size=5,
        vad_filter=True,
        **kwargs,
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


def _transcribe(
    audio_path, model_name, language, fmt, task="transcribe", initial_prompt="", hotwords=""
):
    """Transcribe (or translate to English) audio. Tries CUDA first, then CPU."""
    result = _transcribe_faster_whisper(
        audio_path,
        model_name,
        language,
        task=task,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
    )
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
        task = config.get("task") or "transcribe"
        initial_prompt = config.get("initial_prompt") or ""
        hotwords = config.get("hotwords") or ""
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
            print(f"Transcribing: {model_name} (task={task})", file=sys.stderr)
            start = time.time()
            transcript = _transcribe(
                audio_path,
                model_name,
                language,
                fmt,
                task=task,
                initial_prompt=initial_prompt,
                hotwords=hotwords,
            )
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
