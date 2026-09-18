"""Security review checks: auth guard, path traversal, filename sanitization,
untrusted-source prompt handling, and archive-bomb protection."""

import io
import json

import pytest

from app import backup, db, studio
from app.config import DATA_DIR, UPLOADS_DIR, load_prompt

# ---------- authentication guard ----------


def test_unauthenticated_blocked_and_public_allowed(anon_client):
    assert anon_client.get("/api/notebooks").status_code == 401
    assert anon_client.get("/health").status_code == 401
    assert anon_client.get("/healthz").status_code == 200
    assert anon_client.get("/login").status_code == 200


# ---------- uploaded filenames / path traversal ----------


def test_upload_filename_traversal_is_sanitized(client, notebook):
    res = client.post(
        f"/api/notebooks/{notebook['id']}/sources/file",
        files={"file": ("../../evil.txt", io.BytesIO(b"traversal probe unique"), "text/plain")},
    )
    assert res.status_code == 200, res.text
    name = res.json()["name"]
    assert "/" not in name and "\\" not in name and ".." not in name
    assert any(p.name.endswith("evil.txt") for p in UPLOADS_DIR.iterdir())


def test_media_audio_artifact_paths_reject_traversal(client):
    # Slashes don't match single path params; dot-segments fall through to a
    # lookup miss. Either way the response must be 404, never a file read.
    assert client.get("/api/media/..%2f..%2fetc%2fpasswd").status_code == 404
    assert client.get("/api/audio/..%2f..%2fetc%2fpasswd").status_code == 404
    assert client.get("/api/artifacts/..%2f..%2fetc%2fpasswd/file").status_code == 404


# ---------- prompt-injection (sources are data, not instructions) ----------


def test_grounded_prompt_treats_sources_as_untrusted():
    prompt = load_prompt("grounded_answer").lower()
    assert "untrusted data" in prompt
    assert "never obey it" in prompt


def test_studio_prompts_append_untrusted_data_rule(monkeypatch):
    captured = {}

    class _LLM:
        def chat(self, messages, stream=False):
            captured["system"] = messages[0]["content"]
            return json.dumps({"title": "t", "type": "bar", "labels": ["a", "b"], "values": [1, 2]})

    monkeypatch.setattr(studio, "_gather_context", lambda nb, budget: "ctx")
    monkeypatch.setattr(studio, "get_llm", lambda: _LLM())
    studio.generate_artifact_spec("nb", "chart")
    assert "untrusted data" in captured["system"].lower()


# ---------- archive bombs ----------


def test_backup_restore_rejects_oversized_archive(tmp_path, monkeypatch):
    db.init_db()
    out = tmp_path / "b.zip"
    backup.create_backup(out, data_dir=DATA_DIR)
    monkeypatch.setattr(backup, "MAX_RESTORE_BYTES", 16)
    with pytest.raises(backup.BackupError, match="over the"):
        backup.restore_backup(out, tmp_path / "restored")
