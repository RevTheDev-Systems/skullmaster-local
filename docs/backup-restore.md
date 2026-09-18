# Backup and recovery

SkullMaster keeps three kinds of state, backed up separately:

| Kind | Where | In the backup? |
|---|---|---|
| Source code | git repository | No (use git) |
| Configuration | `.env` | Optional (`--include-config`; may contain secrets) |
| Runtime state | `data/` (SQLite, LanceDB, uploads, audio, artifacts) | Yes |

## Create

```bash
uv run python -m app.backup create --out ~/skullmaster-backup.zip
# include configuration too (contains no passwords, but may hold URLs/paths):
uv run python -m app.backup create --out ~/skullmaster-backup.zip --include-config
```

The archive contains `data/…`, an optional `config/.env`, and a `manifest.json`
with a SHA-256 per file. The SQLite database is copied with SQLite's online
**backup API**, so the snapshot is transactionally consistent even while the
server is running. (LanceDB files are copied best-effort; stopping the server
first gives the cleanest snapshot.)

## Restore

```bash
uv run python -m app.backup restore --archive ~/skullmaster-backup.zip --target /tmp/restored
```

Restore verifies every checksum, refuses path-traversal entries, and then
**rewrites the absolute upload/artifact paths** recorded in the database so the
restored copy finds its own files under `--target`. To use the restored data,
point the app at it with `NLM_DATA_DIR=/tmp/restored` (or move it into place).

## Verification (a backup is only real once restored)

`tests/test_backup.py` proves the round trip in a clean temporary environment:
seed a notebook + chat + cached-upload + audio + artifact, back up, restore into
an empty directory, then confirm the notebook, message, and files reappear and
that DB paths were rewritten. It also proves the archive rejects checksum
tampering and path-traversal entries.

## Notes

- Restoring to the **same** data directory is exact; restoring elsewhere relies
  on the path rewrite above.
- The backup does not include local model weights (`models/`) — those re-download
  on demand.
- LanceDB and SQLite both live under `data/`; back up that one directory and you
  have the whole knowledge base.
