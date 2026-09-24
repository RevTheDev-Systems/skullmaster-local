# Status

Current state of SkullMaster iQ, reconciling the docs with the application.
See `README.md` for usage and the architecture overview.

## Implemented (tested)

- **Ingestion** — PDF (page numbers), DOCX (structure + tables), XLSX/XLSM
  (sheet/row), TXT/MD/RST/CSV/TSV/JSON, HTML, and URL; **YouTube URLs ingest the
  caption transcript** with timestamps (citations open the video at the cited
  moment); audio/video transcription with timestamps. Article pages that yield
  only boilerplate are refused, not ingested. All parse failures are
  `IngestError` → HTTP 422. Matrix and fixtures: `docs/ingestion-matrix.md`,
  `tests/test_ingest.py`.
- **Retrieval** — structure-aware chunking, LanceDB vectors + BM25 fused with
  reciprocal rank fusion. Benchmark + committed baseline in `evals/`.
- **Grounded chat** — closed-world prompt, SSE streaming, validated inline
  `[n]` citations, persisted conversation with `completed` / `interrupted`
  turn states.
- **Cross-notebook research** — answer across the whole library with a
  diversity-merged retrieval pass and notebook-attributed citations (transient,
  not persisted to a notebook).
- **Library search** — semantic + keyword search across every notebook
  (`GET /api/search`), grouped by notebook, opening the exact passage.
- **Studio** — Audio Overview; chart / infographic / spreadsheet / mind graph;
  source comparison (each position attributed to a real source); and the text
  artifacts (briefing, study guide, FAQ, timeline, source summary).
  Strict server-side spec validation with one retry on malformed model output;
  model refusals are final. Knowledge-graph nodes and edges are typed
  (controlled vocabularies), bound to source evidence, explorable (pan/zoom,
  click-to-focus), and can span the whole library (cross-notebook graphs).
- **Providers** — Ollama + MLX behind a capability router. Preferred vs. active
  model, live discovery, provider states, cached metadata, failure isolation,
  and consumer-aware routing with a fallback policy (`GET /api/models/route`).
- **Authentication** — PBKDF2 password, server-side sessions, login throttling,
  loopback-first bind, timezone-aware expiry.
- **Persistence** — SQLite + LanceDB + files; deletion cleans up; data survives
  restart.
- **OCR (optional)** — scanned PDF pages are OCR'd only when a PDF's text layer
  is insufficient and a Tesseract engine is installed; never applied to text
  PDFs or by default. See `docs/ingestion-matrix.md`.
- **Vision-document understanding** — image sources and images embedded in PDFs
  are transcribed/described by a vision-capable local model (`qwen2.5vl:7b`
  installed) and flow into retrieval/citations; opt-in, bounded, and it never
  changes the active chat model. See `docs/vision.md`.
- **Slides & Video Overview** — a grounded slide deck (with an in-app navigator)
  and a narrated MP4 built from it (slide images + local TTS, muxed with ffmpeg;
  requires `ffmpeg` on PATH).
- **Local tools (Phase 22)** — a controlled, auditable tool layer
  (`calculator`, `days_between`, `convert`, `word_count`) with strict argument
  validation and no code execution, exposed via `/api/tools` and
  `/api/tools/run`, and wired into research answers as an opt-in, logged
  **multi-step** tool loop (`use_tools`, bounded by `TOOL_MAX_ROUNDS`), including
  source-scoped `count_in_sources` / `find_in_sources`. See `docs/tools.md`.
- **Diagnostics** — `python -m app.diagnostics` (config classification, storage,
  SQLite, providers, TTS/STT, OCR).
- **Backup/restore** — `python -m app.backup create|restore`: a coherent,
  checksummed snapshot of the data directory with path relocation and
  path-traversal protection; round trip proven in `tests/test_backup.py`.
- **Engineering gates** — ruff lint/format, mypy, pytest (mock providers), CI
  (`github/workflows/ci.yml`), all green.

## Experimental

- **MLX capability inference** — reasoning support for MLX models is inferred
  from naming hints because `/v1/models` exposes no capabilities; treat
  `can_reason` for MLX as a heuristic.
- **Retrieval tuning** — fusion weighting is tuned (BM25 1.5 lifted recall@3 to
  1.0) and a chunk size/overlap sweep (`--sweep`) shows all configurations
  identical on the current corpus (saturated). Reranking/query expansion remain
  future, gated on a larger corpus.

## Planned

See `docs/roadmap.md`. The rich knowledge graph, semantic search, capability
router, tool layer, and vision-document understanding are delivered; remaining
items are source-scoped tools/multi-step tool budgets, reranking/query expansion
on a larger corpus, and a saved standalone graph workspace.

## Out of scope

- Deep Research / web search (closed-world by design).
- Cloud/telemetry of any kind.
