# Releasing

Releases are cut by pushing a `v*` tag. The `Release` workflow
(`.github/workflows/release.yml`) then produces distributable artifacts.

## What a release produces

- A **GitHub Release** named after the tag, with auto-generated notes and two
  attached assets:
  - `skullmaster-iq-<tag>.tar.gz` — the tracked source (`git archive`, so it
    never contains `.env`, `data/`, `models/`, or other untracked files).
  - `SHA256SUMS` — the tarball checksum.
- A **container image** pushed to GitHub Container Registry:
  - `ghcr.io/revthedev-systems/skullmaster-local:<tag>`
  - `ghcr.io/revthedev-systems/skullmaster-local:latest`

`workflow_dispatch` runs the packaging steps without creating a release or
pushing an image (useful for a dry run).

## Cutting a release

1. Update the version in `app/config.py`, `pyproject.toml`, and
   `scripts/install_app.sh`; refresh `uv.lock` (`uv lock`).
2. Add `docs/releases/v<version>.md` and, if behavior changed, update
   `docs/status.md` / `docs/roadmap.md` / `README.md`.
3. Run the gates:
   ```bash
   uv run ruff check app tests && uv run ruff format --check app tests
   uv run mypy app && uv run pytest -q
   ```
4. Commit, tag, and push:
   ```bash
   git commit -am "release: v<version>"
   git tag -a v<version> -m "SkullMaster iQ v<version>"
   git push origin main && git push origin v<version>
   ```
5. Confirm the `Release` workflow succeeded (Actions tab) and the assets/image
   appear.

## Using an artifact

- **Tarball:** download, extract, then `./scripts/setup.sh` and
  `uv run python -m app` (see the README Install section).
- **Container:**
  ```bash
  docker run --rm -p 127.0.0.1:8501:8501 \
    --add-host=host.docker.internal:host-gateway \
    -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    -v "$PWD/data":/app/data -v "$PWD/models":/app/models \
    ghcr.io/revthedev-systems/skullmaster-local:latest
  ```
  (GHCR packages are private by default; make the package public in the
  repository's package settings if you want anonymous pulls.)

## Not yet automated

A signed macOS `.dmg` and a Homebrew tap would require an Apple Developer
certificate and a separate tap repository; both are deliberate future steps.
