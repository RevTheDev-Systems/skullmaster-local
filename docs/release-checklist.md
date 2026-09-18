# Release checklist

Run for every release candidate and stable release. "Auto" means an automated
test covers it; "Manual" is the browser/operator pass.

## Gates (must be green)

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
uv run pytest -q
```

## Candidate procedure

| Step | How | Coverage |
|---|---|---|
| Clean install | fresh clone + `uv sync` | Auto (CI) |
| Configure | `cp .env.example .env` | Auto (`config_report`) |
| Launch | `uv run python -m app` | Auto (lifespan tests) · Manual |
| Authenticate | first-run setup, sign in | Auto (`test_auth`) |
| Ingest each format | upload PDF/DOCX/XLSX/TXT/MD/HTML/URL, audio/video | Auto (`test_ingest`, `test_api`) |
| Chat + citations | ask, click `[n]` | Auto (API) · Manual |
| Model switching | picker + persistence | Auto (`test_models`) · Manual |
| Studio | audio + all nine artifact kinds | Auto (`test_media_artifacts`, `test_api`) · Manual render |
| Restart + persistence | new lifecycle, data intact | Auto (`test_api`) |
| Diagnostics | `python -m app.diagnostics` | Auto (`test_diagnostics`) |
| Backup + restore | create, restore into clean dir | Auto (`test_backup`) |
| Evaluation baseline | `python -m app.evaluation` | Run at release; compare to `evals/baseline.json` |
| Performance baseline | `python -m app.benchmarks` | Run at release; compare to `evals/benchmarks-baseline.json` |
| Browser matrix | `docs/browser-acceptance.md` | Manual |

## Release record (attach to the tag)

- version, commit SHA, Python version, schema version
- supported providers and formats
- test count/result
- evaluation + performance baselines
- known limitations and rollback instructions

## Rollback

The pre-stabilization state is preserved on the `baseline/pre-skullmaster-stabilization`
branch (commit `882f7d4`). Data is portable via `data/` (or a backup zip), and
schema migrations are additive, so rolling code back keeps existing data
readable.
