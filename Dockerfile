# ── Python runtime ───────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps: ffmpeg (for yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" pydantic aiosqlite aiofiles \
    structlog aiohttp wasmtime cryptography kaggle faster-whisper yt-dlp modal huggingface_hub

# Copy application source (package lives in src/scrapower/)
COPY src/ src/
COPY deploy/ deploy/

# Patch kagglesdk bug: TimeDeltaSerializer crashes on "0s" values
RUN python3 /app/deploy/patch_kagglesdk.py /usr/local/lib/python3.12/site-packages/kagglesdk/kaggle_object.py

# Data directory (mounted as volume in production)
RUN mkdir -p data/blobs && chown -R 1000:1000 /app

USER 1000:1000

EXPOSE 8777

ENV SCRAPOWER_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV HOME=/app
ENV KAGGLE_CONFIG_DIR=/tmp/.kaggle
ENV PYTHONPATH=/app/src

CMD ["python", "-m", "scrapower.coordinator.main"]
