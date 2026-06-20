FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir aiohttp wasmtime

# Copy worker code (files are at the repo root in HF Spaces layout)
COPY worker/ /app/worker/
COPY app.py /app/app.py

EXPOSE 7860

ENV COORDINATOR_URL="wss://scrapower.talos-int.com/worker/ws"
ENV SCRAPOWER_API_KEY=""

CMD ["python", "/app/app.py"]
