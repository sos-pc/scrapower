# ── Stage 1: Build browser worker (TypeScript → JS) ────────────
FROM node:22-alpine AS builder
WORKDIR /build
COPY worker-browser/package.json worker-browser/package-lock.json ./
RUN npm ci
COPY worker-browser/src/ ./src/
RUN npx esbuild src/index.ts --bundle --outfile=dist/worker.js --format=esm \
    && npx esbuild src/sandbox_worker.ts --outfile=dist/sandbox_worker.js --format=esm \
    && npx esbuild src/sw.ts --outfile=dist/sw.js --format=esm

# ── Stage 2: Python runtime ────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" pydantic aiosqlite aiofiles \
    structlog aiohttp wasmtime cryptography

# Copy application source (package lives in src/scrapower/)
COPY src/ src/

# Copy built browser worker from Stage 1
COPY --from=builder /build/dist/worker.js src/scrapower/coordinator/static/worker.js
COPY --from=builder /build/dist/sandbox_worker.js src/scrapower/coordinator/static/sandbox_worker.js
COPY --from=builder /build/dist/sw.js src/scrapower/coordinator/static/sw.js

# Data directory (mounted as volume in production)
RUN mkdir -p data/blobs

EXPOSE 8777

ENV SCRAPOWER_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

CMD ["python", "-m", "scrapower.coordinator.main"]
