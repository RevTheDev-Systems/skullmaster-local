# 💀 SkullMaster iQ

A fully local, offline NotebookLM-style app: source-grounded chat with inline
citations and podcast-style Audio Overviews — running entirely on your machine
via [Ollama](https://ollama.com). No cloud APIs, no telemetry, no tracking.

## Features

- **Notebooks & sources** — upload PDF, DOCX, TXT/MD, XLSX, video, or audio files, or add URLs; multiple sources per notebook
- **Video & audio sources** — uploads are transcribed locally with Whisper, playable in-app, and fully searchable in chat; media citations carry timestamps and a "Play from" button that seeks the player to the cited moment
- **Closed-world RAG chat** — answers come ONLY from your sources; off-corpus questions are declined instead of hallucinated
- **Inline citations** — every claim carries a clickable `[n]` chip that opens the exact source passage (with page numbers for PDFs, timestamps for media)
- **Audio Overview** — a two-host podcast conversation about your sources, synthesized with a local TTS model, playable and downloadable in the UI
- **Grounded documents** — Studio also writes a **briefing document, study guide, FAQ, timeline, and source summary** from your sources, each downloadable as Markdown; like every generator, they must cite the sources or refuse
- **Charts, infographics, spreadsheets & mind graphs** — bar/line/pie charts, infographics, and mind graphs (downloadable as SVG), plus extracted data tables (downloadable as XLSX/CSV); numbers are validated to come from the sources, and the model refuses when the notebook has no usable data
- **Source-grounded knowledge graph** — the Mind Graph is a radial concept graph (central topic, colour-coded theme branches, concepts, cross-links) whose nodes are bound to the exact source passage they came from, shown in an Evidence list
- **Persistent** — notebooks, sources, chat history, audio overviews, and artifacts survive refreshes and restarts
- **Password-protected** — a sign-in screen guards every route and API endpoint; the password is stored only as a salted PBKDF2 hash on this machine
- **Themed UI** — dark by default with a light theme one click away, and a mobile layout with a bottom tab bar

## Quick start

```bash
# Prereqs: Ollama running, uv installed
cp .env.example .env      # adjust models if desired
uv sync
uv run python -m app      # binds HOST/PORT from .env (default 127.0.0.1:8501)
# open http://127.0.0.1:8501
```

`HOST`/`PORT` come from `.env` and are the single source of truth: the
`python -m app` entry point, `scripts/launcher.sh`, and its health check all
read them, so there are no hardcoded addresses to keep in sync.

On first boot the app checks the models configured in `.env` and pulls any that
are missing (progress is logged; startup won't silently hang). Kokoro TTS weights
(~340MB) download once on the first Audio Overview. `scripts/launcher.sh` starts
the server if needed and opens the UI.

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
requires a valid session — including `/health` and every `/api/*` route.

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
since they can't answer chat.

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
configured models, provider states, MLX (optional), TTS, STT, and whether the
API server is listening — each with an actionable fix. `/health` (or
`/api/health`) returns the same readiness as structured JSON. Log categories are
documented in `docs/observability.md`.

## Local data: where it lives, backup, reset

Everything is stored under `data/` (override with `NLM_DATA_DIR`):

| Path | Contents |
|---|---|
| `data/notebooks.db` | SQLite: notebooks, sources, chat messages, audio + artifact metadata |
| `data/lancedb/` | Vector index (chunk text + embeddings) |
| `data/uploads/` | Original uploaded files (including video/audio, served for playback) |
| `data/audio/` | Generated Audio Overview WAVs |
| `data/artifacts/` | Generated spreadsheet .xlsx files |
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
    briefing_spec.txt       grounded briefing document
    study_guide_spec.txt    grounded study guide
    faq_spec.txt            grounded FAQ
    timeline_spec.txt       grounded timeline
    source_summary_spec.txt grounded source summary
  ingest.py        PDF (PyMuPDF), DOCX (python-docx), XLSX (openpyxl), HTML
                   (trafilatura + fallback), text, video/audio (Whisper),
                   hardened URL fetch; failures normalized to IngestError
  chunker.py       paragraph-packing chunker (~800 tok, overlap), page metadata
  store.py         LanceDB vector store + BM25, reciprocal-rank-fusion hybrid search
  rag.py           retrieval → grounded prompt → streamed cited answer
  studio.py        podcast + all grounded artifacts (visual and text) + evidence binding
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
evals/             RAG benchmark corpus + committed baseline
docs/              status, browser acceptance, ingestion matrix, knowledge graph
data/              runtime state: uploads, LanceDB, SQLite, generated audio/artifacts
models/            local TTS/STT weights
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
- Scanned (image-only) PDFs are rejected — there is no OCR.
- URL extraction targets article-like pages; heavily scripted pages may yield nothing.
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

See `docs/status.md` for the current implemented / experimental / planned /
out-of-scope inventory. In short: the self-hosted local app is implemented and
tested; OCR, richer cross-notebook knowledge graphs, and a fuller model
capability router are planned; video/slides and web search are out of scope.
The launcher and `.app` are location-independent; repository/brand identity and
the explicit rename path are in `docs/naming.md`.

## Future work (intentionally out of scope)

- Video Overviews and slide decks (separate rendering pipelines, deliberately
  not bolted onto `studio.py`)
- Deep Research / web search (the app is closed-world by design)
- OCR for scanned documents (planned as a capability, never applied by default)
