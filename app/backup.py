"""Coherent local backup and restore.

SkullMaster separates:

* **source code** — the git repository,
* **configuration** — `.env` (optional in a backup),
* **runtime state** — SQLite metadata, LanceDB index, uploaded sources,
  generated audio, and generated artifacts (all under the data directory).

A backup is a single zip containing a `data/` tree, an optional `config/.env`,
and a `manifest.json` with a SHA-256 per file. The SQLite database is copied
with the sqlite backup API so the snapshot is transactionally consistent even
while the server is running. Restore verifies every checksum and refuses
path-traversal entries, so a corrupt or hostile archive cannot write outside the
target.

CLI:
    python -m app.backup create  --out backup.zip [--include-config]
    python -m app.backup restore --archive backup.zip --target DIR [--include-config]
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1
SQLITE_NAME = "notebooks.db"


class BackupError(Exception):
    """Recoverable backup/restore failure."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Transactionally consistent copy via SQLite's online backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source:
        with sqlite3.connect(dst) as target:
            source.backup(target)


def create_backup(
    out_path, *, data_dir=None, config_path=None, include_config: bool = False
) -> dict:
    """Write a coherent snapshot zip and return its manifest."""
    from .config import APP_VERSION, DATA_DIR, PRODUCT_NAME, PROJECT_ROOT

    data_dir = Path(data_dir or DATA_DIR)
    if not data_dir.exists():
        raise BackupError(f"Data directory does not exist: {data_dir}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="skullmaster-backup-") as tmp:
        staging = Path(tmp)
        staged_data = staging / "data"

        # Copy everything except the live SQLite DB, which is snapshotted safely.
        for item in data_dir.rglob("*"):
            if item.is_dir():
                continue
            if item.name.startswith(SQLITE_NAME):  # notebooks.db, -wal, -shm
                continue
            rel = item.relative_to(data_dir)
            dest = staged_data / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)

        sqlite_src = data_dir / SQLITE_NAME
        if sqlite_src.exists():
            _copy_sqlite(sqlite_src, staged_data / SQLITE_NAME)

        if include_config:
            cfg = Path(config_path) if config_path else (PROJECT_ROOT / ".env")
            if cfg.exists():
                dest = staging / "config" / ".env"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cfg, dest)

        files: dict[str, dict] = {}
        for item in sorted(staging.rglob("*")):
            if item.is_file():
                rel = str(item.relative_to(staging))
                payload = item.read_bytes()
                files[rel] = {"size": len(payload), "sha256": _sha256(payload)}

        manifest = {
            "format": FORMAT_VERSION,
            "product": PRODUCT_NAME,
            "version": APP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data_dir": str(data_dir),
            "file_count": len(files),
            "files": files,
        }
        (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(staging / MANIFEST_NAME, MANIFEST_NAME)
            for rel in files:
                zf.write(staging / rel, rel)

    return manifest


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise BackupError(f"Refusing unsafe archive path: {name}")
    return path


def restore_backup(
    archive, target_data_dir, *, include_config: bool = False, config_target=None
) -> dict:
    """Restore `archive` into `target_data_dir`, verifying every checksum."""
    archive = Path(archive)
    if not archive.exists():
        raise BackupError(f"Archive not found: {archive}")
    target = Path(target_data_dir)

    with zipfile.ZipFile(archive) as zf:
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME))
        except KeyError as e:
            raise BackupError("Not a SkullMaster backup (no manifest.json)") from e
        if manifest.get("format") != FORMAT_VERSION:
            raise BackupError(f"Unsupported backup format: {manifest.get('format')}")
        expected = manifest.get("files", {})

        for info in zf.infolist():
            if info.is_dir() or info.filename == MANIFEST_NAME:
                continue
            name = _safe_member(info.filename)
            payload = zf.read(info)
            want = expected.get(info.filename)
            if want is None:
                raise BackupError(f"File not in manifest: {info.filename}")
            if _sha256(payload) != want["sha256"]:
                raise BackupError(f"Checksum mismatch: {info.filename}")

            top = name.parts[0]
            if top == "data":
                dest = target.joinpath(*name.parts[1:])
            elif top == "config":
                if not include_config:
                    continue
                dest = Path(config_target) if config_target else (target.parent / ".env")
            else:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)

    # The DB stores absolute paths for uploads and artifact files; rewrite them
    # so a restore into a different directory still finds its files.
    _rewrite_absolute_paths(target / SQLITE_NAME, manifest.get("data_dir"), target)
    return manifest


def _rewrite_absolute_paths(db_path: Path, old_data_dir, new_data_dir: Path) -> None:
    if not old_data_dir or not db_path.exists():
        return
    old_root = Path(old_data_dir)
    if old_root == new_data_dir:
        return
    with sqlite3.connect(db_path) as conn:
        for table, column in (("sources", "stored_path"), ("artifacts", "file_path")):
            rows = conn.execute(
                f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall()
            for row_id, value in rows:
                try:
                    rel = Path(value).relative_to(old_root)
                except ValueError:
                    continue
                conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE id = ?",
                    (str(new_data_dir / rel), row_id),
                )


def _fmt(manifest: dict) -> str:
    return (
        f"{manifest.get('product')} v{manifest.get('version')} backup — "
        f"{manifest.get('file_count')} files, created {manifest.get('created_at')}"
    )


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m app.backup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="write a backup zip")
    create.add_argument("--out", required=True)
    create.add_argument(
        "--include-config", action="store_true", help="also include .env (may contain secrets)"
    )

    restore = sub.add_parser("restore", help="restore a backup zip")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--target", required=True, help="data directory to restore into")
    restore.add_argument("--include-config", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            manifest = create_backup(args.out, include_config=args.include_config)
            print(f"Wrote {args.out}\n  {_fmt(manifest)}")
        else:
            manifest = restore_backup(args.archive, args.target, include_config=args.include_config)
            print(f"Restored to {args.target}\n  {_fmt(manifest)}")
    except BackupError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
