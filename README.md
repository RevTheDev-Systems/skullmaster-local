# NotebookLM Local

A local, offline clone of Google NotebookLM's base functionality: source-grounded
chat with inline citations and podcast-style Audio Overviews — running entirely on
your machine via [Ollama](https://ollama.com).

## Features

- **Notebooks & sources** — upload PDF, DOCX, TXT/MD, or add URLs; multiple sources per notebook
- **Closed-world RAG chat** — answers come ONLY from your sources; off-corpus questions are declined instead of hallucinated
- **Inline citations** — every claim carries a `[n]` citation that opens the exact source passage (with page numbers for PDFs)
- **Audio Overview** — a two-host podcast conversation about your sources, synthesized with a local TTS model
- **100% local** — no external API calls at runtime

## Setup

```bash
# Prereqs: Ollama running, uv installed
cp .env.example .env      # adjust models if desired
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8501
# open http://127.0.0.1:8501
```

On first boot the app checks the models configured in `.env` and pulls any that
are missing. Kokoro TTS weights (~340MB) download once on first Audio Overview.

## Swapping models (the whole point of the provider layer)

All model selection lives in `.env` — zero code edits to swap:

| Variable | Purpose | Examples |
|---|---|---|
| `CHAT_MODEL` | Answering + podcast scripts | `qwen3:30b`, `phi4`, `llama3.3` |
| `EMBED_MODEL` | Chunk/query embeddings | `nomic-embed-text`, `qwen3-embedding`, `bge-m3` |
| `TTS_MODEL` | Audio Overview voices | `kokoro` (Kokoro-82M), `say` (macOS built-in) |
| `TTS_VOICE_A/B` | The two podcast hosts | kokoro: `af_heart`/`am_michael`; say: `Samantha`/`Daniel` |
| `OLLAMA_BASE_URL` | LLM backend endpoint | `http://localhost:11434` |
| `LLM_PROVIDER` | Backend implementation | `ollama` (an OpenAI-compatible provider can be added in `app/providers/`) |

**Note:** if you change `EMBED_MODEL`, re-ingest your sources — embeddings from
different models are not comparable.

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
  ingest.py        PDF (PyMuPDF), DOCX (python-docx), text, URL (trafilatura)
  chunker.py       paragraph-packing chunker (~800 tok, overlap), page metadata
  store.py         LanceDB vector store + BM25, reciprocal-rank-fusion hybrid search
  rag.py           retrieval → grounded prompt → streamed cited answer
  studio.py        podcast script generation + TTS synthesis + WAV assembly
  db.py            SQLite metadata (notebooks, sources)
  main.py          FastAPI endpoints + SSE chat streaming
static/            three-panel web UI (Sources | Chat | Studio), vanilla JS
data/              runtime state: uploads, LanceDB, SQLite, generated audio
models/            local TTS weights
```

**Why LanceDB:** embedded (no server process), columnar with fast ANN and
SQL-style metadata filtering, and the whole index is one portable directory.

**Retrieval:** hybrid — vector similarity + BM25 keyword, merged with reciprocal
rank fusion. Citations are validated server-side: any `[n]` the model emits that
doesn't map to a retrieved excerpt is stripped.

## Future work (intentionally out of scope)

- Video Overviews and slide decks
- Deep Research / web search (the app is closed-world by design)
- Studio text artifacts: briefing docs, study guides, FAQs, timelines, mind maps
- Whisper ingestion for audio/video sources
