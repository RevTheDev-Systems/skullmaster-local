"""Backup/restore: coherent snapshot, verification, and clean-environment restore."""

import json
import sqlite3
import zipfile

import pytest

from app import backup, db
from app.config import ARTIFACTS_DIR, AUDIO_DIR, DATA_DIR, UPLOADS_DIR


@pytest.fixture(autouse=True)
def _db_ready():
    db.init_db()  # these tests use the DB without the HTTP client fixture


def _seed_runtime() -> dict:
    nb = db.create_notebook("Backup Notebook")
    db.add_message(nb["id"], "user", "hello backup")

    upload = UPLOADS_DIR / "seed.txt"
    upload.write_text("source content")
    db.create_source(
        nb["id"], "seed.txt", "text", "seed.txt", None, 1, status="ready", stored_path=str(upload)
    )

    (AUDIO_DIR / "seed.wav").write_bytes(b"RIFFfake-audio")
    db.create_audio_overview(nb["id"], "seed.wav", "Seed", 1.0, 1)

    artifact = ARTIFACTS_DIR / "seed_chart.xlsx"
    artifact.write_bytes(b"PKfake-xlsx")
    db.create_artifact(
        nb["id"], "chart", "Seed Chart", json.dumps({"title": "Seed Chart"}), str(artifact)
    )
    return nb


def test_backup_restore_roundtrip(tmp_path):
    nb = _seed_runtime()
    out = tmp_path / "backup.zip"

    manifest = backup.create_backup(out, data_dir=DATA_DIR)
    assert out.exists()
    assert manifest["file_count"] > 0
    assert "data/notebooks.db" in manifest["files"]
    assert manifest["data_dir"] == str(DATA_DIR)

    # "destroy" the environment and restore into a clean directory
    target = tmp_path / "restored"
    backup.restore_backup(out, target)

    con = sqlite3.connect(target / "notebooks.db")
    con.row_factory = sqlite3.Row
    assert (
        con.execute("SELECT name FROM notebooks WHERE id=?", (nb["id"],)).fetchone()["name"]
        == "Backup Notebook"
    )
    assert (
        con.execute("SELECT content FROM messages WHERE notebook_id=?", (nb["id"],)).fetchone()[
            "content"
        ]
        == "hello backup"
    )

    # files present in the restored data dir
    assert (target / "uploads" / "seed.txt").read_text() == "source content"
    assert (target / "audio" / "seed.wav").exists()
    assert (target / "artifacts" / "seed_chart.xlsx").exists()

    # absolute DB paths are rewritten to the restore location
    stored = con.execute(
        "SELECT stored_path FROM sources WHERE notebook_id=?", (nb["id"],)
    ).fetchone()["stored_path"]
    assert stored.startswith(str(target))
    file_path = con.execute(
        "SELECT file_path FROM artifacts WHERE notebook_id=?", (nb["id"],)
    ).fetchone()["file_path"]
    assert file_path.startswith(str(target))
    con.close()


def test_restore_rejects_checksum_mismatch(tmp_path):
    backup.create_backup(tmp_path / "b.zip", data_dir=DATA_DIR)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(tmp_path / "b.zip") as src, zipfile.ZipFile(bad, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "data/notebooks.db":
                data += b"tamper"
            dst.writestr(info, data)
    with pytest.raises(backup.BackupError, match="Checksum"):
        backup.restore_backup(bad, tmp_path / "restored")


def test_restore_rejects_path_traversal(tmp_path):
    with pytest.raises(backup.BackupError, match="unsafe"):
        backup._safe_member("../../etc/passwd")
    with pytest.raises(backup.BackupError, match="unsafe"):
        backup._safe_member("/absolute/evil")


def test_create_backup_missing_dir_raises(tmp_path):
    with pytest.raises(backup.BackupError):
        backup.create_backup(tmp_path / "x.zip", data_dir=tmp_path / "missing")


def test_backup_can_include_config(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CHAT_MODEL=example\n")
    out = tmp_path / "b.zip"
    manifest = backup.create_backup(out, data_dir=DATA_DIR, config_path=env, include_config=True)
    assert "config/.env" in manifest["files"]

    cfg_out = tmp_path / "restored.env"
    backup.restore_backup(out, tmp_path / "restored", include_config=True, config_target=cfg_out)
    assert cfg_out.read_text() == "CHAT_MODEL=example\n"
