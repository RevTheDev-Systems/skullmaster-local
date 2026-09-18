# Status

Current state of SkullMaster iQ, reconciling the docs with the application.
See `README.md` for usage and the architecture overview.

## Implemented (tested)

- **Ingestion** — PDF (page numbers), DOCX (structure + tables), XLSX/XLSM
  (sheet/row), TXT/MD/RST/CSV/TSV/JSON, HTML, and URL; audio/video transcription
  with timestamps. All parse failures are `IngestError` → HTTP 422. Matrix and
  fixtures: `docs/ingestion-matrix.md`, `tests/test_ingest.py`.
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
  (controlled vocabularies) and bound to source evidence.
- **Providers** — Ollama + MLX behind a capability router. Preferred vs. active
  model, live discovery, provider states, cached metadata, failure isolation.
- **Authentication** — PBKDF2 password, server-side sessions, login throttling,
  loopback-first bind, timezone-aware expiry.
- **Persistence** — SQLite + LanceDB + files; deletion cleans up; data survives
  restart.
- **OCR (optional)** — scanned PDF pages are OCR'd only when a PDF's text layer
  is insufficient and a Tesseract engine is installed; never applied to text
  PDFs or by default. See `docs/ingestion-matrix.md`.
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
  1.0); chunk sizing and reranking remain un-tuned until the benchmark justifies
  changes.

## Planned

See `docs/roadmap.md` for the full post-v1 plan. Highlights:

- **Richer knowledge graph** on top of the current evidence-bound nodes.
- **Semantic notebook search** and an expanded capability router.
- Further **retrieval tuning** driven by the committed RAG benchmark.

## Out of scope

- Video Overviews and slide decks.
- Deep Research / web search (closed-world by design).
- Cloud/telemetry of any kind.
