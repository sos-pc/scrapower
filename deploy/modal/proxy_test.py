import os
"""Minimal Modal proxy test - runs yt-dlp and prints results."""

import subprocess
import sys

proxy = os.environ.get("WG_PROXY", "")

print("=== Test: yt-dlp with proxy ===", flush=True)
r = subprocess.run(
    [
        sys.executable,
        "-m",
        "yt_dlp",
        "-f",
        "bestaudio",
        "--no-playlist",
        "--no-warnings",
        "--proxy",
        proxy,
        "-o",
        "/tmp/test.audio",
        "https://youtu.be/jNQXAC9IVRw",
    ],
    capture_output=True,
    text=True,
    timeout=25,
)
print(f"RC={r.returncode}", flush=True)
if r.returncode != 0:
    print(f"STDERR_LAST: {r.stderr[-400:]}", flush=True)
else:
    print("SUCCESS", flush=True)

print("=== yt-dlp version ===", flush=True)
v = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True)
print(f"VERSION={v.stdout.strip()}", flush=True)
print("DONE", flush=True)
