# Browser acceptance matrix

SkullMaster iQ is a local, single-user app. This matrix defines the acceptance
pass for a release. The **API-layer acceptance journey** is automated
(`tests/test_api.py::test_full_acceptance_journey`); the browser rows are the
manual pass an operator runs against the installed app (`open` the launcher
URL, sign in with the owner password).

## Core journey

| # | Step | Expected | Covered by |
|---|------|----------|------------|
| 1 | Open `/` unauthenticated | redirect to `/login` | automated (auth tests) |
| 2 | First run: set password | logged in, app loads | automated (auth tests) |
| 3 | Create/rename notebook | appears in picker | automated (API) · browser manual |
| 4 | Upload each format (PDF/DOCX/XLSX/TXT/MD/HTML) | source indexed, `ready` | automated (`test_ingest`) |
| 5 | Upload audio/video | transcript with timestamps | automated (media, STT mocked) |
| 6 | Ask a question | streamed answer with clickable `[n]` chips | automated (API) · browser manual |
| 6b | Enable **All notebooks**, ask | answer across the library, citations name the notebook | automated (`/api/research`) · browser manual |
| 6c | Landing page: **Create account** ↔ **Return to logon** | switches between setup and sign-in | automated (`test_auth`) · browser manual |
| 6d | **🔍 Search** library, open a hit | results grouped by notebook; opens the passage and switches notebook | automated (`/api/search`) · browser manual |
| 6e | Search modal **🧠 Graph** | graph across all notebooks, evidence names the notebook | automated (`/api/research/graph`) · browser manual |
| 6f | **🧮 Tools** toggle, arithmetic question | one local tool runs (logged); grounded answer with citations, marked transient | automated (`/api/research` use_tools) · browser manual |
| 7 | Click a citation | passage/page/“Play from mm:ss” | automated (payload) · browser manual |
| 8 | Switch model in header | active model persists across restart | automated (models) · browser manual |
| 9 | Audio Overview | 2-host WAV generated + player | automated (API) · browser manual |
| 10 | Chart / Infographic / Spreadsheet | rendered, downloadable | automated (API) · browser manual |
| 11 | Mind Graph | typed radial graph, Evidence list, pan/zoom, click-to-focus | automated (API) · browser manual |
| 12 | Text artifacts (briefing/study guide/FAQ/timeline/summary) | rendered, Markdown download | automated (API) · browser manual |
| 12b | Slides | navigator (◀ ▶, arrow keys), Markdown download | automated (API) · browser manual |
| 12c | Video Overview | narrated MP4 plays in-app (requires ffmpeg) | automated (API, real ffmpeg) · browser manual |
| 13 | Delete source / artifact / notebook | gone, files cleaned | automated (API) |
| 14 | Restart the server | notebooks, chat, artifacts persist | automated (API restart) |

## Layout, themes, content

| Case | Expected | Status |
|------|----------|--------|
| Desktop layout | three-panel, no overflow | manual |
| Mobile layout (bottom nav) | panels switch, buttons fit | manual |
| Light theme | legible contrast | manual |
| Dark theme | legible contrast | manual |
| Long filenames | truncated label, full title on hover | manual |
| Unicode content/filenames | preserved | automated (`test_ingest`, API) |
| Long chat (many turns) | scrolls, history intact | manual |

## Failure modes

| Case | Expected | Status |
|------|----------|--------|
| Failed job (model/embedding down) | clear error toast; turn kept as `interrupted` | automated (API) |
| Backend loss mid-generation | `event: error`, partial answer retained | automated (API) |
| Slow generation | progress/spinner, no timeout crash | manual |
| Corrupt/oversized upload | 422 / 413, no crash | automated (`test_ingest`, API) |
| Duplicate upload | 409 with the existing source name | automated (API) |
| Model backend down at startup | app boots, header shows fallback warning | automated (Phase 1) |

## Out of scope for this release

Slides and Video Overviews (separate rendering pipelines) and OCR (see
`docs/ingestion-matrix.md`).
