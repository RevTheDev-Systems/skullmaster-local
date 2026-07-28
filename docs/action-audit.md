# SkullMaster iQ — Action & Button Audit

Every interactive control in the application, audited 2026-07-08 against the
running app (real Ollama models, real browser). "Tested" means the actual
workflow was exercised, not just the code read.

Global behaviors that apply to every control:
- All buttons are real `<button>` elements (keyboard operable, focus-visible outline).
- Errors surface as toasts or inline message bubbles — never silent, never `alert()`.
- No placeholder/dead controls exist (the former disabled Studio placeholders were removed).
- No unhandled console errors observed across the full E2E run.

| Screen | Control (accessible name) | Action | Backend / behavior | Loading | Success | Error | Disabled logic | Tested |
|---|---|---|---|---|---|---|---|---|
| Login | Password field | Owner password entry | — | — | — | Inline error region | — | ✅ |
| Login | Confirm password (setup only) | Confirm new password | Client-side match check | — | — | "The two passwords don't match." | Hidden outside first-run setup | ✅ |
| Login | Create password / Sign in (submit) | First-run setup or sign-in | `POST /api/auth/setup` / `/api/auth/login` | Button disabled, "Creating…"/"Signing in…" | Redirect to `/` | Inline error (wrong password, too short, 429 lockout, server unreachable); fields cleared | Guarded against double-submit | ✅ |
| Header | Sign out | End session | `POST /api/auth/logout` | Button disabled | Redirect to `/login` | Redirects anyway (fail-safe) | — | ✅ |
| Header | Notebook select | Switch notebook | `GET sources/messages/audio-overviews` | — | Panels reload | Toast | — | ✅ |
| Header | ＋ New | Create notebook | `POST /api/notebooks` | — | Select updates + success toast | Toast | — | ✅ |
| Header | ✏️ Rename | Rename notebook (prompt + confirm) | `PATCH /api/notebooks/{id}` | — | Select label updates + toast | Toast | No-op when unchanged/empty | ✅ |
| Header | 🗑 Delete | Delete notebook (confirm dialog) | `DELETE /api/notebooks/{id}` | — | Next notebook loads + toast | Toast | No-op without selection | ✅ |
| Header | ⟳ Refresh model status | Re-check health | `GET /api/health` | "checking…" | Model badge text | Red badge (Ollama unreachable / model missing) | — | ✅ |
| Header | 🌙/☀️ Theme toggle | Light ⇄ dark theme | localStorage `skullmaster-theme` | — | Instant theme + icon swap | n/a | — | ✅ |
| Sources | 📄 Upload file | Open file picker, ingest | `POST …/sources/file` | Button disabled + progress banner (n/m for multi-file) | Source row + toast | Toast per file (422 unsupported/empty, 409 duplicate, 413 too large, 503 embed) | Disabled while any ingestion runs | ✅ |
| Sources | URL input + Add (form submit) | Fetch & ingest URL | `POST …/sources/url` | Inputs disabled + progress banner | Row + toast, input cleared | Toast (non-http(s) rejected, timeout, size cap, duplicate) | Disabled while ingesting; `required` blocks empty | ✅ |
| Sources | ↻ Retry indexing (failed rows only) | Re-index failed source | `POST …/sources/{id}/retry` | Button disabled | Row turns ready + toast | Toast, row stays failed with error text | Only rendered for `status=failed` | ✅ (via automated test; UI path shares handler) |
| Sources | ✕ Remove source (confirm) | Delete source + vectors + stored file | `DELETE …/sources/{id}` | Button disabled | Row removed, counts update | Toast | — | ✅ (vector removal verified in LanceDB) |
| Chat | Question input + Send (form submit) | Grounded cited answer | `POST …/chat` (SSE) | Send disabled, "Searching sources…" bubble, streamed tokens | Cited answer, chips clickable, persisted | Inline red error bubble | Disabled while streaming; empty input no-op | ✅ |
| Chat | Citation chip [n] | Open source passage | Client-side (citations payload) | — | Modal with source name + page + exact excerpt | n/a (invalid markers stripped server-side) | — | ✅ |
| Chat | Clear chat (confirm) | Wipe notebook chat history | `DELETE …/messages` | — | Empty state shown | Toast | No-op when history empty | ✅ |
| Modal | ✕ Close | Close citation modal | Client-side | — | Modal hidden | n/a | — | ✅ (click, backdrop click, and Esc) |
| Studio | 🎙 Generate Audio Overview | Script + TTS → WAV | `POST …/audio-overview` | Disabled + pulse + status text | New list entry + success toast | Toast (422 no sources, 409 already running) | Disabled while generating; server lock blocks parallel jobs from other tabs | ✅ (real qwen3:30b + kokoro, 143.8s WAV) |
| Studio | Audio player | Play/pause/seek | `GET /api/audio/{file}` | Browser native | Playback | Browser native | — | ✅ (played, paused, seeked to 60s) |
| Studio | ⬇ Download WAV | Save audio file | Same endpoint, `download` attr | Browser native | File saved | Browser native | — | ✅ (200, audio/wav) |
| Sources | ▶ Play (video/audio rows) | Open media player modal | `GET /api/media/{source_id}` (Range-capable) | — | Modal with `<video>`/`<audio>`, autoplays | Toast on media load error | Only rendered for ready media sources | ✅ (real mp4 played in modal) |
| Media modal | ✕ Close | Stop playback, close modal | Client-side | — | Player paused + removed | n/a | — | ✅ (click, backdrop, Esc) |
| Citation modal | ▶ Play from mm:ss (media citations) | Seek player to cited moment | Client-side + media endpoint | — | Citation closes, player opens at timestamp | Toast on media error | Hidden for non-media citations | ✅ (seeked and played from cited second) |
| Studio | 📊 Chart | Grounded chart from sources | `POST …/artifacts {kind:chart}` | All 3 buttons disabled + pulse + progress banner | List entry + success toast + auto-opens view | Toast (422 no sources/no numeric data, 409 running, model failure) | Disabled while any artifact generates; server per-kind lock | ✅ (real qwen3:30b, grounded values) |
| Studio | 🪧 Infographic | Grounded infographic | `POST …/artifacts {kind:infographic}` | Same as chart | Same | Same | Same | ✅ (real generation, rendered SVG) |
| Studio | 📋 Spreadsheet | Grounded data table + .xlsx | `POST …/artifacts {kind:spreadsheet}` | Same as chart | Same; .xlsx materialized server-side | Same | Same | ✅ (real generation; xlsx verified with openpyxl) |
| Studio | Artifact title (list) | Open artifact view modal | Client-side render of stored spec | — | SVG chart/infographic or HTML table | n/a (specs validated server-side) | — | ✅ |
| Studio | ✕ Delete artifact (confirm) | Remove artifact + file | `DELETE …/artifacts/{id}` | Button disabled | Row removed | Toast | — | ✅ (via automated test; UI shares handler) |
| Artifact modal | ⬇ SVG | Download rendered SVG | Client-side XMLSerializer blob | — | .svg saved | n/a | Charts/infographics only | ✅ (serialization verified) |
| Artifact modal | ⬇ CSV | Download table as CSV | Client-side blob | — | .csv saved | n/a | Spreadsheets only | ✅ |
| Artifact modal | ⬇ XLSX | Download real Excel file | `GET /api/artifacts/{id}/file` | Browser native | .xlsx saved | 404 if file missing | Spreadsheets only | ✅ (200, correct content-type, round-trip read) |
| Artifact modal | ✕ Close | Close artifact view | Client-side | — | Modal hidden | n/a | — | ✅ (click, backdrop, Esc) |

## Authentication behavior verified

- **First run** → `/` redirects to `/login`, which renders in "Create your password" mode.
- **Password rules** → under 8 characters rejected server-side; mismatched confirm
  rejected client-side before any request is sent.
- **Setup cannot be replayed** → a second `POST /api/auth/setup` returns 409, so an
  existing install can't be taken over.
- **Guard** → every `/api/*` route and `/health` return 401 without a session;
  HTML navigations get a 303 to `/login` instead. Only `/login`, `/static/*`,
  `/healthz`, and the three auth endpoints are public.
- **Wrong password** → 401 with "Incorrect password", fields cleared, no redirect.
- **Throttling** → 5 failures locks login for 60s (429 with remaining time).
- **Session hardening** → cookie is `HttpOnly` (confirmed unreadable from
  `document.cookie`), `SameSite=Lax`, `Path=/`; only the token's SHA-256 hash is
  stored, and logout invalidates it server-side (a replayed cookie still 401s).
- **Expiry** → past-dated sessions are rejected and purged.
- **Persistence** → session survives reload and server restart; expired/absent
  session sends the SPA to `/login` via the shared 401 handler.

## Edge cases exercised

- **Empty notebook chat** → model declines ("couldn't find this in your sources").
- **Unanswerable question with sources** → declined with explanation, no hallucination.
- **Answerable question** → correct answer, valid clickable `[1]` citation opening
  the exact PDF passage with page number.
- **Duplicate upload** → 409 toast "Already in this notebook as …".
- **file:// URL** → 422 "Only http:// and https:// URLs can be ingested".
- **Double-click Generate Audio** → second click is a no-op (client) and 409 (server).
- **Refresh mid-session** → notebooks, sources, chat (with citation chips), audio list all restore.
- **Full server restart** → all data persists.
- **Rename → remove source → delete notebook** → vectors, uploads, audio files,
  messages, and metadata rows all verifiably removed.
- **Mobile width (≤980px)** → single-column layout, no overflow.
- **Keyboard** → all visible controls focusable, labeled, Esc closes every modal.

## Video/artifact E2E (added 2026-07-09, real models end to end)

A 26-second MP4 with synthesized speech (Kokoro voice, known facts about a
fictional "Meridian solar array") was built and pushed through the real system:

- **Upload via UI** → busy state, then `🎬 meridian_briefing.mp4 · 1 chunks`.
- **Whisper transcription** (faster-whisper base, first-use weight download) →
  transcript matched the spoken script essentially verbatim.
- **Playback** → ▶ opened the player modal; video autoplayed; close stops it.
- **Q&A** → "How much did the Meridian solar array cost to build, and how many
  homes does it power?" → "…cost $940 million [1] and powers roughly 800,000
  homes [1]" — correct, cited.
- **Timestamped citation** → chip opened "meridian_briefing.mp4 — at 0:00" with
  the transcript excerpt and a "▶ Play from 0:00" button that closed the
  citation and started the video at the cited moment.
- **Chart / Infographic / Spreadsheet** → all three generated by qwen3:30b with
  only source-grounded numbers (1.2 GW, 14 km², 800k homes, $940M, 45 days);
  chart and infographic rendered as SVG, spreadsheet as a table plus a real
  .xlsx verified by re-opening it with openpyxl.
- **Persistence** → after a full page refresh, the video source, chat with
  citation chips, and all three artifacts reloaded from SQLite.
- **No console errors or warnings** across the whole run; layout verified at
  mobile width.
