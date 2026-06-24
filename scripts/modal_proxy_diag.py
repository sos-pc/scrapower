"""
Modal proxy diagnostic — run inside coordinator container.
Tests: curl SOCKS5, yt-dlp with/without proxy, yt-dlp version.
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("MODAL_TOKEN_ID", os.environ.get("MODAL_TOKEN_ID", ""))
os.environ.setdefault("MODAL_TOKEN_SECRET", os.environ.get("MODAL_TOKEN_SECRET", ""))

import modal

app = modal.App.lookup("scrapower", create_if_missing=True)

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.0-runtime-ubuntu22.04", add_python="3.12")
    .apt_install("curl", "ffmpeg")
    .pip_install("yt-dlp")
)

proxy = os.environ.get("SCRAPOWER_WG_PROXY_PUBLIC", "") or os.environ.get("SCRAPOWER_WG_PROXY", "")

test_script = f'''
import subprocess, sys, os
proxy = "{proxy}"

print("=== Test 1: resolve DNS ===", flush=True)
r = subprocess.run(["python3", "-c", "import socket; print(socket.gethostbyname('your-coordinator.example.com'))"], capture_output=True, text=True, timeout=10)
print(f"DNS: {{r.stdout.strip()}}", flush=True)

print("=== Test 2: TCP connect port 1081 ===", flush=True)
r = subprocess.run(["python3", "-c",
    "import socket; s=socket.socket(); s.settimeout(5); s.connect(('your-coordinator.example.com',1081)); print('OPEN'); s.close()"
], capture_output=True, text=True, timeout=10)
print(f"TCP 1081: {{r.stdout.strip()}} err={{r.stderr.strip()[:200]}}", flush=True)

print("=== Test 3: yt-dlp WITH proxy ===", flush=True)
r = subprocess.run(
    [sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "--no-playlist", "--no-warnings",
     "--proxy", proxy, "-o", "/tmp/test_proxy.audio",
     "https://youtu.be/jNQXAC9IVRw"],
    capture_output=True, text=True, timeout=25
)
print(f"RC={{r.returncode}}", flush=True)
print(f"STDERR: {{r.stderr[-400:]}}", flush=True)

print("=== Test 4: yt-dlp WITHOUT proxy ===", flush=True)
r = subprocess.run(
    [sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "--no-playlist", "--no-warnings",
     "-o", "/tmp/test_noproxy.audio", "https://youtu.be/jNQXAC9IVRw"],
    capture_output=True, text=True, timeout=20
)
print(f"RC={{r.returncode}}", flush=True)
print(f"STDERR: {{r.stderr[-300:]}}", flush=True)

print("=== Test 5: yt-dlp version ===", flush=True)
r = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True, timeout=5)
print(f"Version: {{r.stdout.strip()}}", flush=True)

print("=== Test 6: yt-dlp with proxy + verbose ===", flush=True)
r = subprocess.run(
    [sys.executable, "-m", "yt_dlp", "-f", "bestaudio", "--no-playlist",
     "--proxy", proxy, "-o", "/tmp/test_v.audio", "-v",
     "https://youtu.be/jNQXAC9IVRw"],
    capture_output=True, text=True, timeout=25
)
print(f"RC={{r.returncode}}", flush=True)
# Show lines mentioning proxy or error
for line in r.stderr.split(chr(10)):
    low = line.lower()
    if "proxy" in low or "error" in low or "format" in low or "fail" in low:
        print(f"  {{line[:200]}}", flush=True)

print("DONE", flush=True)
'''

sb = modal.Sandbox.create(
    "python3",
    "-c",
    test_script,
    app=app,
    image=image,
    cpu=2,
    memory=4096,
    timeout=300,
    idle_timeout=120,
)
print(f"SANDBOX: {sb.object_id}")
time.sleep(95)
logs = sb.logs()
if logs:
    for line in logs.split("\n")[-50:]:
        print(line)
sb.terminate()
print("TERMINATED")
