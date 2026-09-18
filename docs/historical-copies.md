# Historical-copy reconciliation

Fingerprinted 2026-09-18 against the then-canonical checkout
(`~/notebooklm-local`, since migrated to `~/skullmaster-iq`; 110 source files,
excluding `.git`, `.venv`, `data`, `models`, caches). Method:
SHA-256 per file; a manifest hash over sorted `path:hash` pairs. **Nothing was
deleted** — this report is the verification step that must precede any cleanup.

## Downloads snapshots

| Copy | Files | Manifest | Identical | Differ | Unique files | Classification |
|---|---|---|---|---|---|---|
| `skullmaster-local-main` | 45 | `19fc17478882` | 13 | 32 | 0 | older snapshot |
| `skullmaster-local-main 2` | 47 | `9fd0018b9ea8` | 15 | 32 | 0 | duplicate of #3 |
| `skullmaster-local-main 3` | 47 | `9fd0018b9ea8` | 15 | 32 | 0 | older snapshot |
| `skullmaster-local-main 4` | 48 | `ea16baa33229` | 15 | 32 | 1 (`.DS_Store`) | older snapshot |

Findings:

- All four are **strict subsets** of the canonical tree (63–65 canonical files
  absent, and zero unique source files). None contains unique commits (none is a
  git repository) or unique work.
- Copies **2 and 3 are byte-identical** to each other → one is redundant.
- Copy **4** is the newest and largest (matches the ~2026-07-26 state); its only
  unique file is a macOS `.DS_Store`, which is junk.
- **Nothing unique needs to be extracted.**

## Archives

| Archive | Entries | Classification |
|---|---|---|
| `~/Downloads/skullmaster-ui-redesign.zip` | 47 (28 under `app/`/`static/`) | older UI snapshot; superseded by the merged redesign in git |
| `…/CloudDocs/skullmaster-iq-knowledge-base-2026-08-17.zip` | 6 markdown files | separate knowledge asset; predates linkage to this repo |

## Knowledge assets

The August knowledge-base zip and the Obsidian project stub describe SkullMaster
iQ's purpose/architecture as "unknown", which is now stale. They should be
repointed at this repository and `docs/status.md`. Those files live outside the
repo (`…/CloudDocs/…`, `~/Desktop/obsidian-vault …`) and were **not modified
here** — update them by hand or with a follow-up task so nothing outside version
control changes unexpectedly.

## Recommended cleanup (manual, after backup)

1. Back up `~/Downloads/skullmaster-local-main*` and the archives if desired.
2. Remove the redundant snapshots:
   ```bash
   rm -rf ~/Downloads/"skullmaster-local-main" ~/Downloads/"skullmaster-local-main 2" \
          ~/Downloads/"skullmaster-local-main 3" ~/Downloads/"skullmaster-local-main 4" \
          ~/Downloads/skullmaster-ui-redesign.zip
   ```
3. Repoint the knowledge base and Obsidian note at the canonical repo + `docs/status.md`.

The canonical source of truth is the git repository; these copies add only
confusion and disk usage.
