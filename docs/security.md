# Security review

SkullMaster iQ is local-first: it runs on your machine, talks to a local model
backend, and makes no external calls except the two the user initiates (URL
ingestion and one-time model downloads). This reviews each surface against that
promise. Checks are automated in `tests/test_security.py` and elsewhere.

| Area | Implementation | Verified by |
|---|---|---|
| Network listener | Loopback by default (`HOST=127.0.0.1`); binding elsewhere logs a warning that the owner password is the only guard | `app/__main__.py`; `docs/naming.md` |
| Outbound connections | None at runtime except user-initiated URL fetch + first-use model pulls | `README.md` (URL policy); `ingest._fetch_url` |
| URL ingestion | `http`/`https` only, 20s timeout, 10MB cap, fetched once | `tests/test_ingest.py` |
| Authentication | Owner password (PBKDF2-HMAC-SHA256, 600k, per-install salt); no default password | `tests/test_auth.py` |
| Sessions | Server-side; cookie holds a random token, DB stores only its SHA-256 hash; `HttpOnly`, `SameSite=Lax`; 14-day expiry parsed timezone-aware | `tests/test_auth.py` |
| Login throttling | 5 failures → 60s lockout | `tests/test_auth.py` |
| Route guard | Every `/api/*` and `/health` requires a session; only `/login`, `/static/*`, `/healthz`, and the three auth endpoints are public | `tests/test_security.py` |
| Uploaded filenames | Sanitized (`Path(name).name`, unsafe chars → `_`) and stored under a UUID prefix; stored paths must be inside the uploads dir to be read/deleted | `tests/test_security.py` |
| Path traversal | Media/audio/artifact/backup paths are resolved, confined, and validated; archive entries with `..`/absolute paths are refused | `tests/test_security.py`, `tests/test_backup.py` |
| HTML/SVG rendering | The client never uses `innerHTML` for model/source text — everything is set via `textContent`; SVGs are built with `createElementNS`/`setAttribute` | static/app.js (review) |
| Prompt injection from sources | Sources are explicitly labelled **untrusted data** in the grounded-answer and every Studio prompt; system rules take precedence and source-borne "instructions" are never obeyed | `tests/test_security.py` |
| Archive / file bombs | Restore rejects checksum mismatches, path traversal, and archives whose declared uncompressed size exceeds an 8 GB cap | `tests/test_backup.py`, `tests/test_security.py` |
| Oversized inputs | Upload caps (50MB docs / 1GB media) enforced while streaming; URL byte cap | `tests/test_api.py`, `tests/test_ingest.py` |
| Provider endpoints | Configurable via `.env` only; pointing at a remote endpoint is an explicit operator choice | `config_report()` |
| Logs / diagnostics | No credentials are logged or printed; the password exists only as a hash | `tests/test_diagnostics.py` |

## Notes and accepted risks

- The session cookie is not marked `Secure` because the app is served over
  plain HTTP on loopback; marking it `Secure` would break localhost. If you bind
  to a network interface, put it behind HTTPS or a trusted network.
- There is no account recovery by design; a lost password is reset by clearing
  the owner row (see README), which never exposes existing notebooks.
- Model outputs are validated server-side before reaching the client, so the
  renderer only ever receives well-shaped specs.
