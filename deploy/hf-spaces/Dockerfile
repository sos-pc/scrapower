# HuggingFace Spaces worker — CPU-only Mode B worker for Scrapower.
#
# Design decisions:
#   - No faster-whisper or yt-dlp: this worker targets CPU tasks
#     (WASM, Python). GPU tasks go to Kaggle/Modal via capability matching.
#   - aiohttp for HTTP pull/submit (Mode B protocol).
#   - Python 3.12 slim for small image size (faster cold start).
#   - Port 7860 exposed for HF Spaces health check requirement.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only the dependencies this CPU worker actually uses.
# Whisper/GPU deps are NOT installed — GPU tasks are routed to Kaggle/Modal.
RUN pip install --no-cache-dir aiohttp

# Copy worker runtime files (from src/scrapower/worker/ in the repo).
# The Harvester bundles these into the upload folder before deploying.
# NOTE: app.py is standalone (no imports from worker/).
# The worker/ directory was removed from the Docker build context
# in 2026-06-24 — it only contained dead weight for this image.
COPY app.py /app/app.py

EXPOSE 7860

# Env vars injected by Harvester as HF Space secrets:
#   COORDINATOR_URL  — https://your-coordinator.example.com
#   SCRAPOWER_API_KEY — for auth_level >= 1
ENV SCRAPOWER_API_KEY=""

CMD ["python", "/app/app.py"]
