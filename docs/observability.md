# Diagnostics and observability

## Health report

```bash
uv run python -m app.diagnostics
```

Prints a single authoritative report — **Runtime** (Python, dependencies),
**Configuration** (validated/classified), **Storage** (directories + a real
write test + free disk), **SQLite**, **LanceDB**, **LLM backend** (Ollama,
chat/embed models, provider states), **MLX** (optional), **TTS**, **STT**, and
**Server** (is the API listening). Every line is `PASS` / `WARN` / `FAIL`; the
exit code is `1` only if something required failed.

`GET /health` (authenticated) returns the same readiness as structured JSON;
`GET /healthz` is the unauthenticated liveness probe used by the launcher.

## Log categories

Logs go to stdout (the launcher appends them to `data/server.log`). The logger
name identifies the category, so failures are diagnosable at a glance:

| Category | Logger / signal | Example |
|---|---|---|
| Provider / model backend | `app.providers.ollama_provider`, `app.providers.mlx_provider`, `app.providers.routing` | `Could not list MLX models`, `Model fallback: … is unavailable`, `MLX backend error (500)` |
| Malformed model output (retryable) | `app.studio` | `Script parse failed (attempt 1)`, `Artifact spec parse failed (attempt 1)` |
| Retrieval failure | `skullmaster` | `Retrieval failed for notebook …` |
| Ingestion / indexing failure | `skullmaster` | `Indexing failed for source …` |
| Authentication failure | `skullmaster` | `Failed login attempt from …`, `Owner password created` |
| Streaming / model failure | `skullmaster` | `Chat stream failed` |
| Rendering failure | browser console + toast | server always returns a validated spec, so the client only reports display errors |

Format: `asctime LEVEL logger.name: message`, e.g.

```
2026-09-18 00:00:01,123 WARNING app.providers.routing: Model fallback: qwen3.6-35b is unavailable; using qwen3:30b.
```

## Secrets

Diagnostics and logs never print credentials. The app stores no API keys; the
owner password exists only as a salted PBKDF2 hash and is never logged. Backups
exclude `.env` unless you opt in (`--include-config`).
