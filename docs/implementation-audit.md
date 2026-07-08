# SkullMaster Local — Implementation Audit

Audited 2026-07-08 against the running system (backend started, endpoints exercised,
Ollama + models verified live). Repo was at commit `c976fc8` on `main`, clean tree.

## Completed functionality (verified by running it)

- **Backend boots** — FastAPI app starts, `db.init_db()` runs, model check at startup.
- **Provider layer** — `app/providers/` isolates all model access: `LLMProvider`
  (chat/embed/ensure_models/status) and `TTSProvider` (synthesize/status) protocols;
  Ollama, Kokoro, and macOS `say` implementations. No model names in business logic;
  all selection via `.env` (`CHAT_MODEL`, `EMBED_MODEL`, `TTS_MODEL`, `OLLAMA_BASE_URL`).
- **Health** — `/api/health` returns LLM/TTS/vector-store status; verified `ok: true`
  with qwen3:30b + nomic-embed-text present in Ollama.
- **Notebook create/list/delete** — endpoints work; SQLite persistence verified.
- **Ingestion** — PDF (PyMuPDF, page numbers), DOCX (python-docx incl. tables),
  TXT/MD, URL (trafilatura). Deterministic chunker with overlap; stable SHA-256
  chunk IDs; embeddings via provider; LanceDB storage. Existing data confirms
  PDF+DOCX+TXT sources indexed (5 chunks live in LanceDB).
- **Hybrid retrieval** — vector + BM25 with reciprocal rank fusion.
- **Grounded cited chat** — SSE streaming verified live: sources event → tokens →
  validated final answer with `[n]` citations; server strips citations that don't
  map to a retrieved excerpt; closed-world prompt in `app/prompts/grounded_answer.txt`.
  A live question produced a correct multi-source cited answer.
- **Audio Overview pipeline** — script prompt file, JSON script parse with one retry,
  per-line TTS via provider, WAV assembly with pauses; two generated WAVs exist in
  `data/audio/` from prior runs.
- **Citation modal, chat streaming UI, audio player/download** — present in the UI.

## Partial functionality

- **Duplicate-click protection** — chat send and audio button disable while busy,
  but file upload / URL add do not; double submits possible.
- **Error surfacing** — frontend uses `alert()`; no inline error states, no toasts.
- **Ingestion progress** — a single hint line; no per-source status.
- **Empty states** — sources hint exists; chat and studio have none.
- **/health** — lacks product name, version, persistence/DB status, overall status
  string; not exposed at `/health` (only `/api/health`).

## Broken functionality

- None found — everything implemented works when exercised.

## Missing functionality (required by spec)

1. **Notebook rename** — no endpoint, no UI control.
2. **Chat persistence** — history lives only in JS memory; lost on refresh.
3. **Audio overview metadata persistence** — result vanishes on refresh; no DB table,
   no list endpoint.
4. **Failed-source state + retry** — ingest failures leave nothing to retry.
5. **Duplicate-ingestion prevention** — no content hashing; same file can be added twice.
6. **Upload validation** — no file-size cap.
7. **URL fetch hardening** — no scheme allow-list (file:// etc.), no explicit
   timeout/content-size limits.
8. **Deletion cleanup** — deleting a source/notebook removes chunks + DB rows but
   leaves uploaded files and generated audio on disk.
9. **Diagnostics command** — none.
10. **Tests** — none at all.
11. **Rename to SkullMaster Local** — app is branded "NotebookLM Local" everywhere.
12. **Bright UI** — current theme is dark-only; spec requires light-first design.
13. **Docs** — no implementation/action audit docs; README lacks data-path, backup,
    reset, diagnostics, URL-policy sections.
14. **Placeholder buttons** — Studio has three disabled "Future work" buttons
    (Briefing Doc, Study Guide, Mind Map); spec forbids placeholder controls.

## Technical debt affecting reliability

- `alert()`/`prompt()`/`confirm()` for all interactions (rename/create kept native
  is acceptable; error alerts are not).
- No global exception handler — unexpected errors could expose stack traces.
- BM25 index rebuilt from all notebook rows per query — fine at this scale, noted.
- Audio generation is synchronous on a worker thread with no per-notebook lock —
  parallel jobs possible from two tabs.

## Planned repair order

1. `audit:` this document.
2. `fix:` backend — rename endpoint, chat/audio persistence, failed-source retry,
   content-hash dedup, upload/URL validation, deletion cleanup, richer `/health`
   (+ `/health` alias), audio job lock, global error handler, `python -m app.diagnostics`.
3. `ui:` rename to SkullMaster Local everywhere + bright light-first redesign
   (dark toggle optional), toasts, per-control states, remove placeholders,
   accessibility + responsive layout.
4. `test:` pytest unit + integration suite (mock providers, temp data dir).
5. E2E acceptance with real models in a real browser (documented in action audit).
6. `docs:` action-audit.md + README rewrite.
