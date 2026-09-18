# Docker

Run SkullMaster iQ in a container while **inference stays on the host** (Ollama),
so no GPU passthrough is required and your models/GPU are used natively.

## Quick start

```bash
# 1) On the host: install Ollama, start it, and pull models
ollama serve
ollama pull qwen3:4b && ollama pull nomic-embed-text

# 2) From the repo
docker compose up --build
# open http://127.0.0.1:8501  (first run asks you to create a password)
```

`docker-compose.yml` publishes the port on **127.0.0.1 only** (local-first) and
points the app at `host.docker.internal:11434`. On Linux the compose file adds
`host.docker.internal:host-gateway` so the same URL resolves.

## What's in the image

- Python 3.12 + dependencies installed from `uv.lock` (frozen), including the
  `ocr` extra.
- `ffmpeg` (Video Overviews) and Tesseract (scanned-PDF OCR), so those features
  work out of the box.
- The app itself. **Models are not baked in** — chat/embedding/vision run on the
  host Ollama, reached over `OLLAMA_BASE_URL`.

## Volumes

| Volume | Purpose |
|---|---|
| `./data:/app/data` | SQLite, LanceDB, uploads, generated audio/artifacts (persistent) |
| `./models:/app/models` | Whisper / Kokoro weights downloaded on first use |

Both default to the repo's `data/` and `models/` directories, so a container and
a local install can share the same state.

## Configuration

Override any `.env` value through the environment (compose reads a local `.env`
automatically):

```yaml
environment:
  CHAT_MODEL: qwen3:8b
  OLLAMA_BASE_URL: http://host.docker.internal:11434
```

Set `HOST=0.0.0.0` (the compose default) so the container listens; the host port
binding remains loopback-only. To expose it on your LAN, change the ports mapping
in `docker-compose.yml` — but read `docs/security.md` first: the owner password
is then the only guard.

## Vision, OCR, and video

- **Vision** (diagrams/charts/images) needs a vision model on the host:
  `ollama pull qwen2.5vl:7b`.
- **OCR** works in the image (Tesseract is installed; Python extras are synced).
- **Video Overviews** work (ffmpeg is installed); they also need a TTS backend —
  Kokoro downloads its weights into `/app/models` on first use.

## Plain Docker (without compose)

```bash
docker build -t skullmaster-iq:local .
docker run --rm -p 127.0.0.1:8501:8501 \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e CHAT_MODEL=qwen3:4b -e EMBED_MODEL=nomic-embed-text \
  -v "$PWD/data":/app/data -v "$PWD/models":/app/models \
  skullmaster-iq:local
```

## Notes

- The container logs `HOST=0.0.0.0 is not loopback` by design; the published port
  is still bound to 127.0.0.1.
- Windows/macOS: `host.docker.internal` resolves automatically; the
  `extra_hosts` line is harmless there.
