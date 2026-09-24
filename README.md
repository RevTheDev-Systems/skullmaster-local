# 💀 SkullMaster iQ

A fully local, offline NotebookLM-style app: source-grounded chat with inline
citations, cross-notebook research, and grounded Studio output (audio, charts,
graphs, documents, slides, video) — running entirely on your machine via
[Ollama](https://ollama.com). No cloud APIs, no telemetry, no tracking.

**Current release: v1.7.0 · 248 automated tests passing · ruff/format/mypy clean.**

## Features

- **Notebooks & sources** — upload PDF, DOCX, XLSX/XLSM, TXT/MD/RST/CSV/TSV/JSON, HTML, images, video, or audio files, or add URLs; multiple sources per notebook
- **YouTube videos** — a YouTube URL ingests the video's **caption transcript** (manual or auto-generated) as timestamped, searchable, citable text — not the page chrome; a citation opens the video at the cited moment. A video with captions disabled falls back to downloading just its **audio** (`yt-dlp`) and transcribing it locally with the same Whisper pipeline, bounded by `YOUTUBE_MAX_MINUTES` (default 120) and switchable via `YOUTUBE_TRANSCRIBE_FALLBACK` (no silent junk)
- **Video & audio sources** — uploads are transcribed locally with Whisper, playable in-app, and fully searchable in chat; media citations carry timestamps and a "Play from" button that seeks the player to the cited moment
- **Scanned PDFs (optional OCR)** — if a PDF has no text layer and Tesseract is installed, its pages are OCR'd into the normal pipeline; text PDFs are never OCR'd, and without an engine the app behaves as before
- **Diagrams & images (optional vision)** — image files (`.png .jpg .jpeg .webp .gif .bmp .tiff .tif`) and images embedded in PDFs are transcribed/described by a vision-capable local model (`qwen2.5vl:7b`) into searchable, citable text; citations open a **View image** viewer
- **Closed-world RAG chat** — answers come ONLY from your sources; off-corpus questions are declined instead of hallucinated
- **Cross-notebook research** — flip on **All notebooks** in the chat bar to answer across your whole library; retrieval is merged with a diversity pass so one notebook can't dominate, and citations name the notebook they came from (research answers are transient, not saved to a notebook)
- **Library search** — 🔍 Search every notebook at once (semantic + keyword); results are grouped by notebook and open the exact passage, jumping to that notebook
- **Inline citations** — every claim carries a clickable `[n]` chip that opens the exact source passage (with page numbers for PDFs, timestamps for media)
- **Audio Overview** — a two-host podcast conversation about your sources, synthesized with a local TTS model, playable and downloadable in the UI
- **Grounded documents** — Studio also writes a **briefing document, study guide, FAQ, timeline, and source summary** from your sources, each downloadable as Markdown; like every generator, they must cite the sources or refuse
- **Slides** — a grounded slide deck (3–15 slides with titles, bullets, and speaker notes) with an in-app navigator and Markdown export
- **Video Overview** — a narrated MP4 built from the slides: each slide is rendered to an image and voiced with local TTS, then muxed with ffmpeg (so it needs `ffmpeg` on PATH)
- **Local tools (Phase 22)** — a safe, auditable tool layer (`calculator`, `days_between`, `convert`, `word_count`, plus source-scoped `count_in_sources` / `find_in_sources`) at `GET /api/tools` / `POST /api/tools/run`; no code execution, strict validation. Opt in with the **🧮 Tools** toggle to let a research answer run a bounded, logged multi-step tool loop (`TOOL_MAX_ROUNDS`, default 2) before answering
- **Charts, infographics, spreadsheets, mind graphs & source comparison** — bar/line/pie charts, infographics, and mind graphs (downloadable as SVG), extracted data tables (downloadable as XLSX/CSV), and a source comparison that shows where sources agree, differ, or add detail (every position attributed to a real source); numbers are validated to come from the sources, and the model refuses when the notebook has no usable data
- **Source-grounded knowledge graph** — the Mind Graph is a radial graph with **typed entities** (concept/organization/person/location/date/metric/event/document) and **typed relations** (causes/part_of/measures/located_at/enables/contradicts/precedes), coloured and labelled accordingly; every node and edge is bound to the exact source passage it came from in an Evidence list; pan/zoom and click-to-focus make it explorable, and **🧠 Graph** in the search modal builds one across all notebooks
- **Persistent** — notebooks, sources, chat history, audio overviews, and artifacts survive refreshes and restarts
- **Password-protected** — a sign-in screen guards every route and API endpoint; the password is stored only as a salted PBKDF2 hash on this machine
- **Themed UI** — dark by default with a light theme one click away, and a mobile layout with a bottom tab bar

## Install

SkullMaster iQ runs on your own workstation. **macOS is best supported; Linux
works from the command line; Windows is untested.**

### Prerequisites

| Requirement | Why | Get it |
|---|---|---|
| Python 3.12 | runtime | `uv python install 3.12` (uv manages it for you) |
| [uv](https://docs.astral.sh/uv/) | installs dependencies, runs the app | see the uv install docs |
| [Ollama](https://ollama.com/download) | local chat + embedding models | install it and keep it running |
| ~3 GB free disk | default model + embeddings | — |

Optional, per feature — the app runs without them and reports a WARN:
`ffmpeg` (Video Overviews), Tesseract + `pytesseract`/`pillow` (scanned-PDF OCR),
and `qwen2.5vl:7b` (diagrams / charts / images).

### One command

```bash
git clone https://github.com/RevTheDev-Systems/skullmaster-local.git
cd skullmaster-local
./scripts/setup.sh        # checks prereqs, uv sync, creates .env, pulls models
uv run python -m app      # then open http://127.0.0.1:8501
```

`scripts/setup.sh` is idempotent and **never overwrites an existing `.env`**.
Skip the model download with `SKULLMASTER_SKIP_MODELS=1`, or install the OCR
extras with `SKULLMASTER_OCR=1`.

### Manual steps (what setup.sh does)

```bash
uv sync                        # install dependencies
cp .env.example .env           # then edit models/ports if you like
ollama pull qwen3:4b           # chat model (see the table below)
ollama pull nomic-embed-text   # embeddings
uv run python -m app           # binds HOST/PORT from .env
```

### Docker (optional)

Run the app in a container while inference stays on the host (Ollama):

```bash
ollama serve && ollama pull qwen3:4b && ollama pull nomic-embed-text
docker compose up --build      # open http://127.0.0.1:8501
```

The image includes ffmpeg and Tesseract; `data/` and `models/` are mounted as
volumes and the port is published on loopback only. See `docs/docker.md`.

### Choosing a chat model

Set `CHAT_MODEL` in `.env`. Approximate download sizes:

| Model | Size | Notes |
|---|---|---|
| `qwen3:4b` | ~2.6 GB | **default** — good balance for most laptops |
| `qwen3:8b` | ~5.2 GB | stronger answers |
| `llama3.2:3b` | ~2.0 GB | very light |
| `phi4` | ~9.1 GB | strong reasoning, no thinking mode |
| `qwen3:30b` | ~18 GB | best quality; needs a large machine |

Keep `EMBED_MODEL` (`nomic-embed-text`, ~274 MB) unless you intend to re-ingest:
changing it invalidates every stored vector.

### First run

On first boot the app checks the configured models and pulls any that are missing
(progress is logged; startup won't silently hang). Kokoro TTS weights (~340 MB)
download once on the first Audio Overview; Whisper weights download on the first
video/audio transcription. The first screen asks you to create a password (see
**Signing in** below). If something required isn't ready (Ollama down, a model
missing), the **⚙ Setup** button shows a checklist with a copy-paste fix for
each item; optional capabilities (ffmpeg, OCR, vision) are listed too.

`HOST`/`PORT` come from `.env` and are the single source of truth: the
`python -m app` entry point, `scripts/launcher.sh`, and its health check all read
them.

`scripts/launcher.sh` is **cross-platform (bash)**: it discovers `uv`, starts the
server on the configured host/port if it isn't already running, and opens the UI
with `open` (macOS) or `xdg-open` (Linux). Useful switches:

| Variable | Effect |
|---|---|
| `SKULLMASTER_NO_OPEN=1` | start the server but don't open a browser |
| `SKULLMASTER_DRY_RUN=1` | print the resolved project path and URL, then exit |
| `SKULLMASTER_HOME=/path` | use a repo checkout at a different location |

On macOS, `scripts/install_app.sh` installs an app bundle into `~/Applications`
that runs the launcher.

### iPhone / iPad (installable app)

SkullMaster iQ is an installable **PWA**: add it to the iOS Home Screen and it
runs full-screen with its own icon — no App Store, no signing. It needs a secure
context, so serve it over HTTPS with **Tailscale Serve** (a trusted
`*.ts.net` certificate, no extra setup):

```bash
# .env: keep HOST=127.0.0.1 (Serve proxies to loopback — nothing on the LAN)
tailscale serve --bg 8501                     # → https://<machine>.<tailnet>.ts.net/
# If the root URL is already taken by another service, use a dedicated port:
tailscale serve --bg --https=8443 http://127.0.0.1:8501
```

Then on the iPhone: open that **https://** URL in **Safari**, sign in, and
**Share → Add to Home Screen**. For "it just works", keep the server running at
login with the launchd LaunchAgent in the doc (remember the `PATH` entry, or
`ffmpeg`/`tesseract` won't be found). Full steps, a recorded reference
configuration, troubleshooting, and undo commands are in
[`docs/iphone-app.md`](docs/iphone-app.md).

## Signing in

The first time you open the app it shows **Create your password** — pick one
(8+ characters) and it becomes the owner password for this install. After that
the same screen asks you to sign in.

- There is **no default password** and no recovery: nothing can be read from the
  app until you set one, and no one (including these docs) knows it.
- The password is stored only as a **PBKDF2-HMAC-SHA256 hash** (600,000 iterations,
  per-install random salt) in `data/notebooks.db`. The plaintext is never written
  anywhere.
- Sessions live server-side; the browser cookie holds a random token and the
  database stores only its SHA-256 hash, so a copied database can't be replayed
  as a login. Cookies are `HttpOnly` + `SameSite=Lax`, and sessions last 14 days.
- Five wrong attempts locks out login for 60 seconds.
- **Forgot it?** There's no back door by design. Reset by clearing the owner row —
  this keeps all your notebooks and only forces a fresh password setup:
  ```bash
  sqlite3 data/notebooks.db "DELETE FROM app_user; DELETE FROM sessions;"
  ```

Everything except the login screen, its assets, and the `/healthz` liveness probe
requires a valid session — including `/health` and every `/api/*` route. The
reviewed security surfaces (listener, traversal, prompt injection, archive bombs,
and more) are documented in `docs/security.md`.

## Swapping models (the whole point of the provider layer)

**From the UI:** the header has a model picker listing every chat-capable model
from **both** engines — Ollama and, when available, MLX. Switching takes effect
immediately and is remembered across restarts (stored in the `settings` table as
the *preferred* model, separate from the *active* runtime model). The ⟳ button
re-reads the model list and readiness.

Models carry capability metadata — chat / reasoning / embedding / vision / tools
— plus context length, and each provider reports a state
(`healthy` / `degraded` / `offline`). Per-model metadata is cached (300s), so a
warm model list is a single `list()` call; the ⟳ button requests `?refresh=1` to
bypass the cache after a model pull. Embedding-only models are filtered out,
since they can't answer chat. The router can also pick a model for a required
capability (and optional minimum context) with a fallback policy — active model
→ first capable model → explicit miss — read-only at
`GET /api/models/route?capability=reasoning&min_context=32000`.

A model going offline is not an application failure: if the saved preference's
backend is unavailable, chat falls back to a reachable Ollama model, keeps the
preference, and shows a warning.

### MLX models (Apple silicon)

Run an OpenAI-compatible MLX server and its models appear in the picker
automatically — no configuration needed:

```bash
mlx_lm.server --host 127.0.0.1 --port 8080
```

- Models are discovered from your local HuggingFace cache via `/v1/models`.
  Image, TTS, and embedding models are filtered out of the picker.
- **Reasoning models are fully supported.** MLX streams chain-of-thought in a
  separate `reasoning` field, which is discarded — only the answer reaches the
  UI, so citations stay clean. This mirrors Ollama's thinking mode.
- The first message after switching loads the model into memory and can take a
  while for large models; later messages are fast.
- **Chat only.** `mlx_lm.server` has no `/v1/embeddings`, so embeddings always
  stay on Ollama. That's deliberate: it keeps your existing vector index valid
  no matter which chat model you pick.

| Variable | Purpose | Default |
|---|---|---|
| `MLX_ENABLED` | `auto` (probe the endpoint live, including after launch), `true`, or `false` | `auto` |
| `MLX_BASE_URL` | OpenAI-compatible endpoint (mlx_lm.server, LM Studio, …) | `http://127.0.0.1:8080/v1` |
| `MLX_REQUEST_TIMEOUT` | Connect timeout in seconds (no read timeout, so long generations aren't cut off) | `30` |

Embedding and TTS models stay in `.env` on purpose: changing `EMBED_MODEL`
invalidates every stored vector, so it shouldn't be a one-click action.

**From `.env`:** all model selection lives here — zero code edits to swap:

| Variable | Purpose | Examples |
|---|---|---|
| `CHAT_MODEL` | Answering + podcast scripts | `qwen3:30b`, `phi4`, `llama3.3` |
| `EMBED_MODEL` | Chunk/query embeddings | `nomic-embed-text`, `qwen3-embedding`, `bge-m3` |
| `TTS_MODEL` | Audio Overview backend | `kokoro` (Kokoro-82M, portable), `say` (macOS built-in) |
| `TTS_VOICE_A/B` | The two podcast hosts | kokoro: `af_heart`/`am_michael`; say: `Samantha`/`Daniel` |
| `STT_MODEL` | Video/audio transcription | `whisper-tiny` … `whisper-large-v3` (faster-whisper; default `whisper-base`) |
| `OLLAMA_BASE_URL` | LLM backend endpoint | `http://localhost:11434` |
| `LLM_PROVIDER` | Backend implementation | `ollama` (Ollama + auto-detected MLX; further OpenAI-compatible backends drop into `app/providers/`) |
| `MAX_UPLOAD_MB` / `MEDIA_MAX_UPLOAD_MB` | Upload caps | documents 50MB / media 1GB by default |
| `HOST` / `PORT` | Bind address used by `python -m app` and the launcher | `127.0.0.1` / `8501` |
| `OCR_ENABLED` / `OCR_LANG` / `OCR_MIN_CHARS_PER_PAGE` / `OCR_DPI` | Optional OCR for text-less PDF pages | `auto` / `eng` / `40` / `200` |
| `YOUTUBE_TRANSCRIBE_FALLBACK` / `YOUTUBE_MAX_MINUTES` | Captions-free YouTube fallback (download audio + local Whisper) and its duration cap | `auto` / `120` |
| `TOOL_MAX_ROUNDS` | Tool rounds a research answer may take before it must answer (1–5) | `2` |

**Note:** if you change `EMBED_MODEL`, re-ingest your sources — embeddings from
different models are not comparable.

**TTS strategy:** neither backend is natively multi-speaker, so the podcast is
assembled per-line — each script line is synthesized with that host's voice and
the clips are joined with natural pauses into one WAV. Both backends sit behind
the same `TTSProvider` interface.

**STT strategy:** video/audio sources are transcribed with faster-whisper
(CTranslate2, CPU int8) behind an `STTProvider` interface. Whisper weights
download once into `models/whisper/` on the first transcription. Bigger sizes
transcribe more accurately but slower; `whisper-base` is a good default for
clear speech.

## Tests, lint, types, and the RAG benchmark

```bash
uv run pytest                 # unit + integration suite (mock providers, temp data dir)
uv run ruff check app tests   # lint
uv run ruff format --check app tests
uv run mypy app               # type check

# RAG evaluation harness — real retrieval/generation over a fixed corpus:
uv run python -m app.evaluation              # configured models, throwaway data dir
uv run python -m app.evaluation --no-generate  # retrieval metrics only (fast)
uv run python -m app.evaluation --sweep        # chunk size/overlap sweep (retrieval)

# Performance benchmarks — startup, discovery, embedding, ingestion, retrieval:
uv run python -m app.benchmarks --sizes small,medium
```

The integration tests run the real FastAPI app against real SQLite/LanceDB in a
throwaway directory with mock LLM/TTS providers and a lexical embedding stub, so
**no models are needed** and CI never downloads multi-gigabyte weights
(`.github/workflows/ci.yml`). The RAG benchmark is documented in `evals/` with a
committed baseline; retrieval is only tuned once the benchmark says so. Browser
acceptance is in `docs/browser-acceptance.md`, ingestion coverage in
`docs/ingestion-matrix.md`, and current status in `docs/status.md`.

## Diagnostics

```bash
uv run python -m app.diagnostics
```

Reports PASS/WARN/FAIL for Python and dependencies, configuration, storage
(with a real write test and free-disk check), SQLite, LanceDB, Ollama and both
configured models, provider states, MLX (optional), TTS, STT, OCR (optional),
vision (optional), and whether the API server is listening — each with an
actionable fix. `/health` (or `/api/health`) returns the same readiness as
structured JSON. Log categories are documented in `docs/observability.md`.

## Local data: where it lives, backup, reset

Everything is stored under `data/` (override with `NLM_DATA_DIR`):

| Path | Contents |
|---|---|
| `data/notebooks.db` | SQLite: notebooks, sources, chat messages, audio + artifact metadata |
| `data/lancedb/` | Vector index (chunk text + embeddings) |
| `data/uploads/` | Original uploaded files (including video/audio/images, served for playback/viewing) |
| `data/audio/` | Generated Audio Overview WAVs |
| `data/artifacts/` | Generated spreadsheet `.xlsx` files and Video Overview `.mp4` files |
| `models/` | Local TTS weights (Kokoro) and Whisper STT weights |

- **Back up:** `uv run python -m app.backup create --out ~/backup.zip` — a
  coherent zip (consistent SQLite snapshot + LanceDB + uploads + audio +
  artifacts, with a checksummed manifest). Restore with
  `uv run python -m app.backup restore --archive ~/backup.zip --target DIR`.
  See `docs/backup-restore.md`; the round trip is verified in `tests/test_backup.py`.
- **Reset:** stop the server and delete `data/` — it is recreated empty on next start.
- **Deletion behavior:** removing a source deletes its vectors and stored upload;
  deleting a notebook also removes its chat history, audio files, and metadata.

## URL ingestion & the offline guarantee

The app makes **no external calls at runtime**, with exactly two user-initiated
exceptions:

1. **URL ingestion** — a URL is fetched **once**, at the moment you explicitly
   submit it. The extracted text is stored locally and never re-fetched. Only
   `http`/`https` schemes are allowed (no `file://` or local reads), with a 20s
   timeout and a 10MB cap. There is no web search, crawling, or background fetching.
2. **First-use model downloads** — Ollama pulls missing models at startup;
   Kokoro TTS weights download on first Audio Overview; Whisper STT weights
   download on first video/audio transcription. All are local-cache-once.

There is no telemetry, analytics, or cloud logging of any kind.

## Architecture

```
app/
  config.py        env-driven config — models are configured, never hardcoded
  providers/       the ONLY place that talks to model backends
    base.py          LLMProvider / TTSProvider / STTProvider interfaces
    routing.py       routes chat to the backend owning the selected model
    ollama_provider.py  chat + embeddings (thinking tokens stripped)
    mlx_provider.py  MLX chat via an OpenAI-compatible endpoint (reasoning discarded)
    tts_kokoro.py    Kokoro-82M via kokoro-onnx (auto-downloads weights)
    tts_say.py       macOS `say` fallback
  prompts/         every quality-critical prompt, as editable text files
    grounded_answer.txt     closed-world cited answering
    podcast_script.txt      two-host audio overview script
    chart_spec.txt          grounded chart extraction (bar/line/pie JSON)
    infographic_spec.txt    grounded infographic extraction
    spreadsheet_spec.txt    grounded tabular-data extraction
    mindgraph_spec.txt      grounded concept map (root/branches/links JSON)
    comparison_spec.txt     grounded source comparison (agree/differ/adds)
    briefing_spec.txt       grounded briefing document
    study_guide_spec.txt    grounded study guide
    faq_spec.txt            grounded FAQ
    timeline_spec.txt       grounded timeline
    source_summary_spec.txt grounded source summary
    slides_spec.txt         grounded slide deck (titles/bullets/notes)
  ingest.py        PDF (PyMuPDF), DOCX (python-docx), XLSX (openpyxl), HTML
                   (trafilatura + fallback), text, video/audio (Whisper),
                   hardened URL fetch; failures normalized to IngestError
  ocr.py           optional Tesseract OCR for text-less PDF pages (never default)
  vision.py        optional vision model reads diagrams/charts/images
  chunker.py       paragraph-packing chunker (~800 tok, overlap), page metadata
  store.py         LanceDB vector store + BM25, RRF hybrid search, library search
  rag.py           retrieval → grounded prompt → streamed cited answer; research
  studio.py        podcast + all grounded artifacts (visual and text) + evidence binding
  slides.py        grounded slide-deck generation + PNG rendering (PyMuPDF)
  video.py         narrated Video Overview: slides + TTS + ffmpeg mux
  tools.py         controlled local tools (calculator/dates/units/text + source-scoped)
  evaluation.py    deterministic RAG benchmark (python -m app.evaluation)
  benchmarks.py    local performance benchmarks (python -m app.benchmarks)
  auth.py          password hashing (PBKDF2), server-side sessions, login throttling
  db.py            SQLite metadata (notebooks, sources, messages, audio, artifacts,
                   owner account, sessions)
  diagnostics.py   python -m app.diagnostics
  backup.py        coherent backup/restore (python -m app.backup)
  __main__.py      python -m app — binds HOST/PORT from config
  main.py          FastAPI endpoints, auth guard middleware, SSE chat streaming
static/            three-panel web UI (Sources | Chat | Studio), vanilla JS
  login.html/.css/.js   sign-in and first-run password setup screen
tests/             pytest unit + integration suite (mock providers)
evals/             RAG corpus + baseline, chunk sweep, performance baseline
docs/              status, roadmap, security, performance, tools, vision, slides-video,
                   docker, releasing, iphone-app, backup, naming, observability,
                   browser acceptance, ingestion matrix, knowledge graph, git history,
                   historical copies, releases/
data/              runtime state: uploads, LanceDB, SQLite, generated audio/artifacts
models/            local TTS/STT weights
scripts/           setup.sh (onboarding) · launcher.sh (start + open) · install_app.sh (macOS app)
Dockerfile / docker-compose.yml   container build (inference stays on host Ollama)
```

**Citation flow:** retrieval returns the top excerpts; the model must cite them
as `[n]`. The server streams the retrieved excerpts to the client first (so chips
can resolve), then the tokens, then a final validated answer in which any `[n]`
that doesn't map to a real excerpt has been stripped. Chips open the exact
passage in a modal. Chat exchanges are persisted with their citation payloads.

**Audio flow:** notebook chunks → grounded two-host script (JSON, retried once
on malformed output) → per-line TTS through the provider → resampled, pause-joined
mono WAV in `data/audio/` → metadata row in SQLite → playable/downloadable in the
Studio panel. A per-notebook server-side lock prevents parallel generation jobs.

**Video/audio source flow:** upload → stored in `data/uploads/` → transcribed
through the STT provider → transcript blocks chunked with their start second in
the location slot → embedded and indexed like any text. Playback streams from
`/api/media/{source_id}` (with HTTP Range support for seeking); a chat citation
on a media source shows its timestamp and can open the player at that moment.

**Artifact flow:** notebook chunks → kind-specific grounded prompt → strict JSON
spec, shape-validated server-side (one retry) → persisted in SQLite → rendered
client-side as SVG (charts, infographics, mind graphs), an HTML table
(spreadsheets, also materialized as a real `.xlsx` in `data/artifacts/`), or a
formatted document (briefing, study guide, FAQ, timeline, summary) with a
Markdown download. Malformed model output is retried; a model refusal is final.
Mind-graph nodes are then bound to the exact source chunk they came from
(`studio.bind_evidence`) and shown in an Evidence list. The prompts forbid
invented facts and instruct the model to refuse when the sources hold no usable
data.

**Why LanceDB:** embedded (no server process), columnar with fast ANN and
SQL-style metadata filtering, and the whole index is one portable directory.

**Retrieval:** hybrid — vector similarity + BM25 keyword, merged with reciprocal
rank fusion.

## Limitations

- Answer quality and decline discipline depend on the configured `CHAT_MODEL`;
  very small models cite less reliably.
- Scanned (image-only) PDFs are OCR'd only if an engine is installed:
  `brew install tesseract` **and** `uv sync --extra ocr` (the Python bindings
  are an optional extra). Without them, scans are rejected with a clear message.
  OCR is never applied to text PDFs.
- Vision-document understanding needs a vision-capable model
  (`ollama pull qwen2.5vl:7b`); without one, images fail with a clear message.
- Video Overview needs `ffmpeg` on PATH; Slides need neither.
- URL extraction targets article-like pages: a page whose readable text is
  under ~200 characters (scripted app shells, sign-in walls) is **refused**
  rather than ingested as navigation/footer chrome.
- YouTube ingestion uses the **caption track**. A video with captions disabled,
  or that is private/region-blocked, is refused — downloading its audio and
  transcribing with Whisper is not wired in.
- Audio Overview generation is synchronous and takes a few minutes; the UI stays
  responsive but the result appears only when finished. Video transcription is
  likewise synchronous (roughly real-time or faster with `whisper-base` on CPU).
- Transcription quality depends on audio clarity and the `STT_MODEL` size;
  videos without speech are rejected ("No speech detected").
- Studio quality depends on the sources actually containing the relevant
  material (numbers, dates, concepts); every generator is instructed to refuse
  rather than invent, so thin notebooks yield refusals instead of content.
- Browser playback supports what the browser supports — MP4/WebM play everywhere;
  MKV/AVI ingest fine but may not play in every browser.
- TTS voices are English-focused (Kokoro `en-us` voices by default).

## Status

See `docs/status.md` for the implemented / experimental / planned /
out-of-scope inventory, and `docs/roadmap.md` for the post-v1 plan. In short,
everything documented is **delivered and tested**: grounded cited chat,
cross-notebook research, library search, typed and evidence-bound knowledge
graphs (explorable and cross-notebook), the model capability router, optional
OCR and vision, Studio (Audio Overview; charts, infographics, spreadsheets, mind
graphs, source comparison; five text documents; slides; narrated video), a safe
local tool layer with a bounded multi-step budget, verified backup/restore,
diagnostics, and engineering gates. The launcher and `.app` are
location-independent; repository/brand identity is in `docs/naming.md`.

## Roadmap & out of scope

Optional future work (see `docs/roadmap.md`): local reranking / query expansion
(gated on a larger evaluation corpus) and a saved standalone graph workspace.

Intentionally out of scope: Deep Research / web search (the app is closed-world
by design), and any cloud or telemetry.

## License

MIT — see [LICENSE](LICENSE). Contributions are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). For security reports, see
[SECURITY.md](SECURITY.md).
