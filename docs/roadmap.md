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

- **OCR** (`app/ingest.py`) — for PDFs whose text layer is insufficient; feeds
  the existing chunk/retrieval pipeline, never applied by default.
- **Retrieval tuning** — act on the Phase 5 benchmark's multi-document Recall@3
  gap: chunk sizing, fusion weighting, and local reranking, measured against
  `evals/`.
- **Source comparison** — ✅ delivered as a `comparison` Studio artifact:
  topics with per-source positions (agree/differ/adds), each attributed to a
  real source or dropped.
- **Cross-notebook research** — ✅ delivered: an "All notebooks" chat mode with
  diversity-merged retrieval and notebook-attributed citations.

## Mid term

- **Richer knowledge graph** — typed entities and relations with evidence-bound
  edges; navigate from the current radial view into a graph explorer.
- **Semantic notebook search** — query the library itself, not just one notebook.
- **More grounded Studio documents** — the local NotebookLM-style set beyond the
  current five, all inheriting the cite-or-refuse rule.
- **Expanded capability router** — consumer-aware routing (chat/reasoning/
  embedding/vision/tools, context length) with fallback policies.

## Longer term (separate pipelines, deliberate)

- **Vision-document understanding** — diagrams/charts/images inside documents.
- **Slides and Video Overviews** — distinct rendering pipelines, explicitly not
  bolted onto `studio.py`.
- **Controlled tool-using research workflows** — bounded, auditable tools under
  the same local-first, source-grounded rules.

## Principles

- Preserve the working system; change one bounded subsystem at a time.
- Measure before tuning (RAG and performance baselines are versioned in `evals/`).
- Closed-world by default: outside the loopback app and user-initiated fetches,
  nothing leaves the machine.
