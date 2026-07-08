# 💀 SkullMaster Local

A fully local, offline NotebookLM-style app: source-grounded chat with inline
citations and podcast-style Audio Overviews — running entirely on your machine
via [Ollama](https://ollama.com). No cloud APIs, no telemetry, no tracking.

## Features

- **Notebooks & sources** — upload PDF, DOCX, TXT/MD, or add URLs; multiple sources per notebook
- **Closed-world RAG chat** — answers come ONLY from your sources; off-corpus questions are declined instead of hallucinated
- **Inline citations** — every claim carries a clickable `[n]` chip that opens the exact source passage (with page numbers for PDFs)
- **Audio Overview** — a two-host podcast conversation about your sources, synthesized with a local TTS model, playable and downloadable in the UI
- **Persistent** — notebooks, sources, chat history, and audio overviews survive refreshes and restarts
- **Bright, accessible UI** — light-first design with an optional dark theme toggle

## Quick start

```bash
# Prereqs: Ollama running, uv installed
cp .env.example .env      # adjust models if desired
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8501
# open http://127.0.0.1:8501
```

On first boot the app checks the models configured in `.env` and pulls any that
are missing (progress is logged; startup won't silently hang). Kokoro TTS weights
(~340MB) download once on the first Audio Overview. `scripts/launcher.sh` starts
the server if needed and opens the UI.

## Swapping models (the whole point of the provider layer)

All model selection lives in `.env` — zero code edits to swap:

| Variable | Purpose | Examples |
|---|---|---|
| `CHAT_MODEL` | Answering + podcast scripts | `qwen3:30b`, `phi4`, `llama3.3` |
| `EMBED_MODEL` | Chunk/query embeddings | `nomic-embed-text`, `qwen3-embedding`, `bge-m3` |
| `TTS_MODEL` | Audio Overview backend | `kokoro` (Kokoro-82M, portable), `say` (macOS built-in) |
| `TTS_VOICE_A/B` | The two podcast hosts | kokoro: `af_heart`/`am_michael`; say: `Samantha`/`Daniel` |
| `OLLAMA_BASE_URL` | LLM backend endpoint | `http://localhost:11434` |
| `LLM_PROVIDER` | Backend implementation | `ollama` (an OpenAI-compatible provider can be added in `app/providers/`) |

**Note:** if you change `EMBED_MODEL`, re-ingest your sources — embeddings from
different models are not comparable.

**TTS strategy:** neither backend is natively multi-speaker, so the podcast is
assembled per-line — each script line is synthesized with that host's voice and
the clips are joined with natural pauses into one WAV. Both backends sit behind
the same `TTSProvider` interface.

## Tests

```bash
uv run pytest            # unit + integration suite (mock providers, temp data dir)
```

The integration tests run the real FastAPI app against real SQLite/LanceDB in a
throwaway directory, with mock LLM/TTS providers so no models are needed. Final
acceptance (real models, real browser) is documented in `docs/action-audit.md`.

## Diagnostics

```bash
uv run python -m app.diagnostics
```

Checks storage directories, SQLite, Ollama reachability, both configured models,
and the TTS backend — and prints an actionable fix for anything that fails.
`/health` (or `/api/health`) returns the same readiness as structured JSON.

## Local data: where it lives, backup, reset

Everything is stored under `data/` (override with `NLM_DATA_DIR`):

| Path | Contents |
|---|---|
| `data/notebooks.db` | SQLite: notebooks, sources, chat messages, audio metadata |
| `data/lancedb/` | Vector index (chunk text + embeddings) |
| `data/uploads/` | Original uploaded files |
| `data/audio/` | Generated Audio Overview WAVs |
| `models/` | Local TTS weights (Kokoro) |

- **Back up:** copy the `data/` directory (it is fully portable).
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
2. **First-use model downloads** — Ollama pulls missing models at startup and
   Kokoro weights download on first Audio Overview; both are local-cache-once.

There is no telemetry, analytics, or cloud logging of any kind.

## Architecture

```
app/
  config.py        env-driven config — models are configured, never hardcoded
  providers/       the ONLY place that talks to model backends
    base.py          LLMProvider / TTSProvider interfaces
    ollama_provider.py  chat + embeddings (thinking tokens stripped)
    tts_kokoro.py    Kokoro-82M via kokoro-onnx (auto-downloads weights)
    tts_say.py       macOS `say` fallback
  prompts/         the two quality-critical prompts, as editable text files
    grounded_answer.txt   closed-world cited answering
    podcast_script.txt    two-host audio overview script
  ingest.py        PDF (PyMuPDF), DOCX (python-docx), text, hardened URL fetch
  chunker.py       paragraph-packing chunker (~800 tok, overlap), page metadata
  store.py         LanceDB vector store + BM25, reciprocal-rank-fusion hybrid search
  rag.py           retrieval → grounded prompt → streamed cited answer
  studio.py        podcast script generation + TTS synthesis + WAV assembly
  db.py            SQLite metadata (notebooks, sources, messages, audio overviews)
  diagnostics.py   python -m app.diagnostics
  main.py          FastAPI endpoints + SSE chat streaming
static/            three-panel web UI (Sources | Chat | Studio), vanilla JS
tests/             pytest unit + integration suite
docs/              implementation audit + action/button audit
data/              runtime state: uploads, LanceDB, SQLite, generated audio
models/            local TTS weights
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
  responsive but the result appears only when finished.
- TTS voices are English-focused (Kokoro `en-us` voices by default).

## Future work (intentionally out of scope)

- Video Overviews and slide decks
- Deep Research / web search (the app is closed-world by design)
- Studio text artifacts: briefing docs, study guides, FAQs, timelines, mind maps
- Whisper ingestion for audio/video sources
