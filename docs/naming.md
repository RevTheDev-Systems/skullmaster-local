# Naming and repository identity

SkullMaster iQ grew out of an earlier "notebooklm-local" prototype, so identities
differ across layers. This document defines the canonical identity and the
explicit migration path. The **governing rule is to preserve the working
system**, so nothing here is applied implicitly.

## Current vs. canonical

| Layer | Current | Canonical target |
|---|---|---|
| Product / brand | SkullMaster iQ | SkullMaster iQ |
| Python distribution (`pyproject`) | `skullmaster-local` | `skullmaster-iq` |
| Import package | `app` | `app` (unchanged) |
| GitHub repo | `revenueroyllc-stack/skullmaster-local` | `skullmaster-iq` |
| Local checkout | `~/notebooklm-local` | `~/skullmaster-iq` |

## Location independence (done)

The launcher and the macOS app no longer assume a fixed path:

- `scripts/launcher.sh` resolves the repo root from its **own location**, with an
  optional `SKULLMASTER_HOME` override.
- `scripts/install_app.sh` bakes the resolved absolute path into the app's
  executable, so the bundle points at wherever the repo actually is.

This means the local checkout can be moved or renamed and the app re-pointed by
re-running `scripts/install_app.sh` — no code edits.

## Explicit migration (manual)

Perform only when ready, and verify at each step:

1. **Back up** `data/` and the repo (a git clone or archive).
2. **Rename the folder**: `mv ~/notebooklm-local ~/skullmaster-iq`.
3. **Re-point the app**: `~/skullmaster-iq/scripts/install_app.sh`.
4. **Verify before/after**: launch and confirm the UI loads, then
   `uv run python -m app.diagnostics` and `uv run pytest -q`.
5. Optionally set `SKULLMASTER_HOME` if the caller can't derive the path.

The GitHub repo rename (Settings → Rename) is a separate, external action; update
the `origin` remote and any clone URLs afterward.

## Why the local folder was not renamed here

The installed `~/Applications/SkullMaster iQ.app` and the running environment
referenced `~/notebooklm-local`. Renaming in place while the system is live risks
breaking the app for no functional gain, so the safe, path-independent
improvements are implemented and the physical rename is left as the explicit,
verified step above.
