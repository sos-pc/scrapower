FROM python:3.12-slim

WORKDIR /app

# Install dependencies in one layer
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic aiosqlite aiofiles structlog aiohttp wasmtime

# Copy config and static files
COPY config/ config/

# Ensure worker.js exists (built locally first)
COPY src/scrapower/coordinator/static/ src/scrapower/coordinator/static/
RUN mkdir -p data/blobs

EXPOSE 8777

ENV SCRAPOWER_HOST=0.0.0.0

CMD ["python", "-m", "scrapower.coordinator.main"]
