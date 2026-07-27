# ── Python runtime ───────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps: ffmpeg (for yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from pyproject — single source of truth for
# versions (this used to be a parallel, unpinned pip list that drifted).
# faster-whisper is deliberately absent: nothing in the coordinator imports it,
# workers install their own (see ModalHarvester's sandbox image spec).
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir ".[drive,providers]"

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
