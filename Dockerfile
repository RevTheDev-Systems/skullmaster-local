# SkullMaster iQ — local-first app container.
#
# Runs the app; inference stays on the host (Ollama) so the container needs no
# GPU. Data and model caches are mounted volumes. Includes ffmpeg (Video
# Overviews) and Tesseract (scanned-PDF OCR) so those features work out of the
# box; vision still needs a vision model on the host Ollama.
FROM python:3.12-slim-bookworm

# uv from the official image (installs deps from uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 ffmpeg tesseract-ocr curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=8501 \
    NLM_DATA_DIR=/app/data

WORKDIR /app

# Dependency layer (cached until pyproject.toml / uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ocr

# Application code.
COPY app ./app
COPY static ./static
COPY scripts ./scripts

# Runtime directories (mounted as volumes in compose).
RUN mkdir -p data models

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/healthz', timeout=4).status == 200 else 1)"

CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "app"]
