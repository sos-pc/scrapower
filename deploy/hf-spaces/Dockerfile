FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir aiohttp wasmtime faster-whisper yt-dlp

# Copy worker code (files are at the repo root in HF Spaces layout)
# Pre-download Whisper tiny model (makes first task fast)
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('tiny', download_root='/tmp/whisper-models')"

COPY worker/ /app/worker/
COPY app.py /app/app.py

EXPOSE 7860

# COORDINATOR_URL must be set via HF Spaces env vars (see README)
ENV SCRAPOWER_API_KEY=""

CMD ["python", "/app/app.py"]
