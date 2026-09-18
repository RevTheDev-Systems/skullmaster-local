# Contributing

Thanks for your interest in SkullMaster iQ. It is a **local-first, single-user**
application, and the guiding rule is *preserve the working system*: change one
bounded subsystem at a time, keep tests green, and measure before tuning.

## Development setup

```bash
git clone <repo> && cd skullmaster-iq
./scripts/setup.sh          # checks prereqs, uv sync, .env, optional model pull
uv run python -m app        # http://127.0.0.1:8501
```

You do **not** need any models to run the test suite — tests use mock providers
and a lexical embedding stub.

## Before you open a pull request

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
uv run pytest -q
```

CI (`.github/workflows/ci.yml`) runs exactly these and never downloads models.

## Guidelines

- **Never commit secrets or runtime data.** `.env`, `data/`, `models/`, and
  generated media are git-ignored — keep it that way.
- **Preserve grounding.** Every generator and the chat path must cite sources or
  refuse; do not add features that let a model assert unsupported facts.
- **Bounded changes.** One subsystem per PR where practical; include tests for
  new behavior and keep the suite green.
- **Measure, don't guess.** Retrieval/generation changes should be validated
  against the committed benchmark (`uv run python -m app.evaluation`); document
  before/after numbers in `docs/performance.md` or the release notes.
- **Optional capabilities stay optional.** OCR, vision, ffmpeg, and MLX must
  degrade gracefully with a clear diagnostic when absent.

## Reporting bugs / requesting features

Open a GitHub issue with steps to reproduce, the output of
`uv run python -m app.diagnostics`, and your OS/Python/model versions. For
security issues, follow [SECURITY.md](SECURITY.md) instead of a public issue.
