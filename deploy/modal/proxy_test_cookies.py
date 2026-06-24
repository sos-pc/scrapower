import os

"""Modal proxy test WITH cookies."""

import os
import subprocess
import sys

proxy = os.environ.get("WG_PROXY", "")
# fetch cookies from coordinator
import urllib.request

coordinator = os.environ["COORDINATOR_URL"]
cookies_hash = os.environ.get("COOKIES_HASH", "")

print(
    f"=== Test with cookies (hash={cookies_hash[:12] if cookies_hash else 'none'}) ===", flush=True
)

cookies_path = None
if cookies_hash:
    cookies_path = "/tmp/cookies.txt"
    try:
        urllib.request.urlretrieve(f"{coordinator}/blobs/{cookies_hash}", cookies_path)
        print(f"Cookies downloaded: {os.path.getsize(cookies_path)} bytes", flush=True)
    except Exception as e:
        print(f"Cookie download failed: {e}", flush=True)
        cookies_path = None

args = [
    sys.executable,
    "-m",
    "yt_dlp",
    "-f",
    "bestaudio",
    "--no-playlist",
    "--no-warnings",
    "--proxy",
    proxy,
]
if cookies_path:
    args += ["--cookies", cookies_path]
args += ["-o", "/tmp/test.audio", "https://youtu.be/jNQXAC9IVRw"]

print(f"CMD: yt_dlp {args[3:]}", flush=True)
r = subprocess.run(args, capture_output=True, text=True, timeout=25)
print(f"RC={r.returncode}", flush=True)
if r.returncode != 0:
    for line in r.stderr.split("\n"):
        if "error" in line.lower() or "format" in line.lower() or "fail" in line.lower():
            print(f"ERR: {line[:200]}", flush=True)
    print(f"FULL_STDERR_TAIL: {r.stderr[-500:]}", flush=True)
else:
    print("SUCCESS", flush=True)
print("DONE", flush=True)
