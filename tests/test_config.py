"""Configuration parsing and classification (Phase 3)."""
from app import config


def test_int_env_falls_back_and_validates(monkeypatch):
    monkeypatch.setenv("PORT", "abc")
    assert config._int_env("PORT", 8501, minimum=1, maximum=65535) == 8501
    monkeypatch.setenv("PORT", "9000")
    assert config._int_env("PORT", 8501, minimum=1, maximum=65535) == 9000
    monkeypatch.setenv("PORT", "70000")
    assert config._int_env("PORT", 8501, minimum=1, maximum=65535) == 8501
    monkeypatch.delenv("PORT")
    assert config._int_env("PORT", 8501, minimum=1, maximum=65535) == 8501


def test_default_configuration_has_no_invalid_entries(monkeypatch):
    for var in ("LLM_PROVIDER", "OLLAMA_BASE_URL", "CHAT_MODEL", "EMBED_MODEL",
                "TTS_MODEL", "STT_MODEL", "MLX_ENABLED", "MLX_BASE_URL",
                "MLX_REQUEST_TIMEOUT", "HOST", "PORT",
                "MAX_UPLOAD_MB", "MEDIA_MAX_UPLOAD_MB"):
        monkeypatch.delenv(var, raising=False)
    report = config.config_report()
    names = {r["name"] for r in report}
    assert {"LLM_PROVIDER", "PORT", "HOST", "CHAT_MODEL", "STT_MODEL"} <= names
    assert [r for r in report if r["status"] == "invalid"] == []


def test_report_flags_invalid_effective_values(monkeypatch):
    monkeypatch.setattr(config, "TTS_MODEL", "espeak")
    monkeypatch.setattr(config, "PORT", 70000)
    monkeypatch.setattr(config, "STT_MODEL", "whisper-enormous")
    status = {r["name"]: r["status"] for r in config.config_report()}
    assert status["TTS_MODEL"] == "invalid"
    assert status["PORT"] == "invalid"
    assert status["STT_MODEL"] == "invalid"


def test_report_flags_invalid_raw_env_even_after_fallback(monkeypatch):
    # The constants already fell back to defaults at import; classification
    # must still surface the raw mistake so it is fixable.
    monkeypatch.setenv("MAX_UPLOAD_MB", "-5")
    monkeypatch.setenv("MLX_REQUEST_TIMEOUT", "soon")
    status = {r["name"]: r["status"] for r in config.config_report()}
    assert status["MAX_UPLOAD_MB"] == "invalid"
    assert status["MLX_REQUEST_TIMEOUT"] == "invalid"


def test_entrypoint_binds_configured_host_and_port(monkeypatch):
    import app.__main__ as entry

    captured = {}
    monkeypatch.setattr(entry.uvicorn, "run",
                        lambda app, **kw: captured.update(kw))
    entry.main()
    assert captured["host"] == entry.HOST
    assert captured["port"] == entry.PORT
