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
- **Studio** — Audio Overview; chart / infographic / spreadsheet / mind graph;
  and the text artifacts (briefing, study guide, FAQ, timeline, source summary).
  Strict server-side spec validation with one retry on malformed model output;
  model refusals are final. Mind-graph nodes are bound to source evidence.
- **Providers** — Ollama + MLX behind a capability router. Preferred vs. active
  model, live discovery, provider states, cached metadata, failure isolation.
- **Authentication** — PBKDF2 password, server-side sessions, login throttling,
  loopback-first bind, timezone-aware expiry.
- **Persistence** — SQLite + LanceDB + files; deletion cleans up; data survives
  restart.
- **Diagnostics** — `python -m app.diagnostics` (config classification, storage,
  SQLite, providers, TTS/STT).
- **Engineering gates** — ruff lint/format, mypy, pytest (mock providers), CI
  (`github/workflows/ci.yml`), all green.

## Experimental

- **MLX capability inference** — reasoning support for MLX models is inferred
  from naming hints because `/v1/models` exposes no capabilities; treat
  `can_reason` for MLX as a heuristic.
- **Retrieval tuning** — chunk sizes, fusion weighting, and the multi-document
  Recall@3 gap are deliberately un-tuned until the benchmark justifies changes.

## Planned

- **OCR** for scanned documents, applied only when a PDF's text layer is
  insufficient (never by default).
- **Richer knowledge graph** — typed entities/relations and cross-notebook
  research on top of the current evidence-bound nodes.
- **Backup/restore operation** (see the data section of the README as this
  lands).

## Out of scope

- Video Overviews and slide decks.
- Deep Research / web search (closed-world by design).
- Cloud/telemetry of any kind.
