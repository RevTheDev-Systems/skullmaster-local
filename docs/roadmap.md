# Post-v1 roadmap

SkullMaster iQ stays the **intelligence/research environment**; models are
replaceable inference engines beneath it. Future work is organized around four
subsystems plus the model fabric:

```
                         SkullMaster iQ
        ┌──────────────────┬──────────────────┬──────────────────┐
   Knowledge Engine    Intelligence Engine       Studio         Model Fabric
   ───────────────     ───────────────────    ────────────      ────────────
   ingestion           RAG + evals            audio             Ollama
   retrieval           synthesis              documents         MLX
   citations           graph reasoning        visualizations    (future: …)
   graph               cross-source           spreadsheets
```

## Near term (build on existing modules)

- **OCR** — ✅ delivered: `app/ocr.py` + `parse_pdf` OCR text-less pages only when
  the text layer is insufficient and Tesseract is installed; never by default.
- **Retrieval tuning** — ✅ delivered: fusion weighting (BM25 1.5 lifted recall@3
  to 1.0) and a chunk size/overlap sweep (`--sweep`, `evals/chunk-sweep.json`)
  that shows retrieval is saturated on the current corpus. Reranking/query
  expansion remain future, gated on a larger corpus.
- **Source comparison** — ✅ delivered as a `comparison` Studio artifact:
  topics with per-source positions (agree/differ/adds), each attributed to a
  real source or dropped.
- **Cross-notebook research** — ✅ delivered: an "All notebooks" chat mode with
  diversity-merged retrieval and notebook-attributed citations.

## Mid term

- **Richer knowledge graph** — ✅ typed entities/relations with evidence-bound
  edges, a graph explorer (pan/zoom, click-to-focus), and cross-notebook graphs
  are delivered.
- **Semantic notebook search** — ✅ delivered: `GET /api/search` searches every
  notebook at once (semantic + keyword), grouped by notebook, opening the exact
  passage.
- **More grounded Studio documents** — the local NotebookLM-style set beyond the
  current five, all inheriting the cite-or-refuse rule.
- **Expanded capability router** — ✅ consumer-aware routing with a fallback
  policy (`route_plan` + `GET /api/models/route`): active model → first capable
  model → explicit miss, with an optional minimum context length.

## Longer term (separate pipelines, deliberate)

- **Vision-document understanding** — diagrams/charts/images inside documents.
- **Slides and Video Overviews** — ✅ delivered as distinct pipelines
  (`slides.py`, `video.py`), not bolted onto `studio.py`: grounded decks + a
  navigator, and a narrated MP4 (slide images + local TTS, muxed with ffmpeg).
- **Controlled tool-using research workflows** — 🟡 started: a safe, auditable
  local tool registry (`app/tools.py`, `/api/tools`) with no code execution;
  wiring tools into the grounded answer path (bounded, logged) is the next step.

## Principles

- Preserve the working system; change one bounded subsystem at a time.
- Measure before tuning (RAG and performance baselines are versioned in `evals/`).
- Closed-world by default: outside the loopback app and user-initiated fetches,
  nothing leaves the machine.
