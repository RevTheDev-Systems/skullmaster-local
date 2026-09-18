# Naming and repository identity

SkullMaster iQ grew out of an earlier "notebooklm-local" prototype, so identities
differ across layers. The **governing rule is to preserve the working system**:
only the local checkout location changed, and only as an explicit, verified
migration. Package, module, and remote names are intentionally unchanged.

## Identity

| Layer | Value | Status |
|---|---|---|
| Product / brand | SkullMaster iQ | canonical |
| Import package | `app` | unchanged |
| Python distribution (`pyproject`) | `skullmaster-local` | intentionally unchanged (no module renames) |
| GitHub repo | `revenueroyllc-stack/skullmaster-local` | intentionally unchanged |
| Local checkout | `~/skullmaster-iq` | **migrated** (was `~/notebooklm-local`) |

The GitHub repository is **not** renamed merely because the local directory
moved; the remote and the local path are independent.

## Local checkout migration (completed 2026-09-18)

| Step | Result |
|---|---|
| Old path | `~/notebooklm-local` (retired — do not recreate) |
| New canonical path | `~/skullmaster-iq` |
| Verified HEAD | `588f388` |
| `.venv` | rebuilt (the old one had absolute shebangs to the old path) |
| macOS app | reinstalled; launcher now runs `~/skullmaster-iq/scripts/launcher.sh` |
| Tests | 233 passed |
| Diagnostics | all required checks passed; version `v1.4.0` |
| Data | resolved under `~/skullmaster-iq/data`; persistence preserved |

`~/notebooklm-local` must not be recreated or used as a fallback checkout.
`.venv.pre-rename` is a preserved rollback environment and must not be used as
the active environment or deleted until final runtime certification.

## Location independence (the enabler)

- `scripts/launcher.sh` resolves the repo root from its **own location**, with an
  optional `SKULLMASTER_HOME` override.
- `scripts/install_app.sh` bakes the resolved absolute path into the app's
  executable, so the bundle points at wherever the repo actually is.

Re-running `scripts/install_app.sh` after a move or rename re-points the app with
no code edits.

## Historical references

The pre-migration name `notebooklm-local` survives only where it is
**intentional history** — for example the dated audit in
`docs/historical-copies.md`. Do not blindly replace such references; obsolete
executable or configuration paths would be a different matter, and the active
code, scripts, `.env*`, and `pyproject.toml` contain none.
