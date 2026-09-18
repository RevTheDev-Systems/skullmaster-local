"""Runtime configuration. All model/backend selection comes from .env — nothing hardcoded in app logic."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

PRODUCT_NAME = "SkullMaster iQ"
APP_VERSION = "1.0.0"

# ---- Paths ----
DATA_DIR = Path(os.environ.get("NLM_DATA_DIR", PROJECT_ROOT / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
LANCEDB_DIR = DATA_DIR / "lancedb"
AUDIO_DIR = DATA_DIR / "audio"
ARTIFACTS_DIR = DATA_DIR / "artifacts"    # generated spreadsheets etc.
MODELS_DIR = PROJECT_ROOT / "models"      # local TTS weights
SQLITE_PATH = DATA_DIR / "notebooks.db"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

for d in (DATA_DIR, UPLOADS_DIR, LANCEDB_DIR, AUDIO_DIR, ARTIFACTS_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _int_env(name: str, default: int, *, minimum: int = 1,
             maximum: int | None = None) -> int:
    """Parse an int env var, falling back to `default` when absent or invalid.

    Invalid values are surfaced by config_report() rather than crashing at
    import — a typo in .env must never stop the app from starting.
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        return default
    return value if value >= minimum else default

# ---- Providers & models (env-only; see .env.example) ----
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3:30b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro")
TTS_VOICE_A = os.environ.get("TTS_VOICE_A", "af_heart")
TTS_VOICE_B = os.environ.get("TTS_VOICE_B", "am_michael")
# Speech-to-text for video/audio sources: "whisper-<size>" via faster-whisper
# (tiny | base | small | medium | large-v3). Weights download once on first use.
STT_MODEL = os.environ.get("STT_MODEL", "whisper-base")

# ---- MLX chat backend (optional, Apple silicon) ----
# Any OpenAI-compatible local endpoint; `mlx_lm.server` by default. When enabled,
# its models appear alongside Ollama's in the model picker. Chat only —
# embeddings always stay on Ollama.
MLX_BASE_URL = os.environ.get("MLX_BASE_URL", "http://127.0.0.1:8080/v1")
MLX_REQUEST_TIMEOUT = _float_env("MLX_REQUEST_TIMEOUT", 30.0, minimum=1.0)
# auto (use it when the endpoint answers) | true (always) | false (never)
MLX_MODE = os.environ.get("MLX_ENABLED", "auto").strip().lower()
_MLX_FALSY = ("0", "false", "no", "off")


def mlx_configured() -> bool:
    """Whether the MLX backend may participate at all (mode != false).

    Reachability is deliberately NOT resolved here: it is probed dynamically by
    the provider layer so an MLX server started after the app is discovered
    without a restart, and an offline MLX endpoint never blocks startup.
    """
    return MLX_MODE not in _MLX_FALSY

HOST = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _int_env("PORT", 8501, minimum=1, maximum=65535)

# ---- Retrieval tuning ----
CHUNK_CHARS = 3200        # ~800 tokens
CHUNK_OVERLAP = 400       # ~100 tokens
TOP_K = 8                 # excerpts handed to the model per question
VECTOR_CANDIDATES = 24    # candidates fetched from each retriever before fusion
CONTEXT_HISTORY_TURNS = 4 # prior chat turns included for conversational context

# ---- Podcast ----
PODCAST_CONTEXT_CHARS = 24_000  # max source characters fed to the script writer

# ---- Ingestion safety limits ----
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_MB", 50) * 1024 * 1024
MEDIA_MAX_UPLOAD_BYTES = _int_env("MEDIA_MAX_UPLOAD_MB", 1024) * 1024 * 1024
URL_FETCH_TIMEOUT = 20          # seconds
URL_MAX_BYTES = 10 * 1024 * 1024

# ---- Studio artifacts ----
ARTIFACT_CONTEXT_CHARS = 24_000  # max source characters fed to artifact generation


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


# ---- Configuration classification ----
# Statuses: required (in use and needed), optional (in use; a sensible default
# exists), deprecated (recognized but no longer used), invalid (unusable value).
# This app has no secrets, and only effective/raw values that matter for
# diagnosis are shown — never credentials.

_LLM_PROVIDERS = ("ollama", "routing", "mlx")
_TTS_MODELS = ("kokoro", "say")
_STT_SIZES = ("tiny", "base", "small", "medium", "large-v3")
_MLX_MODES = ("auto", "true", "false", "1", "0", "yes", "no", "on", "off")


def _invalid_int(name: str, minimum: int, maximum: int | None = None) -> bool:
    """True when the raw env value is present but not a valid int in range."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return False
    try:
        value = int(raw.strip())
    except ValueError:
        return True
    return value < minimum or (maximum is not None and value > maximum)


def _invalid_float(name: str, minimum: float = 0.0) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return False
    try:
        value = float(raw.strip())
    except ValueError:
        return True
    return value < minimum


def _is_http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def config_report() -> list[dict]:
    """Classify the effective configuration as required/optional/invalid.

    Values shown are the ones actually in use (fallback-resolved); raw-env
    mistakes are flagged too, so a bad .env is visible without the app having
    to crash on it. Contains no secrets.
    """
    report: list[dict] = []

    def add(name: str, status: str, detail: str):
        report.append({"name": name, "status": status, "detail": detail})

    # LLM backend
    add("LLM_PROVIDER",
        "required" if LLM_PROVIDER in _LLM_PROVIDERS else "invalid",
        LLM_PROVIDER if LLM_PROVIDER in _LLM_PROVIDERS
        else f"{LLM_PROVIDER!r} (expected: {', '.join(_LLM_PROVIDERS)})")
    add("OLLAMA_BASE_URL",
        "required" if _is_http(OLLAMA_BASE_URL) else "invalid", OLLAMA_BASE_URL)
    for name, value in (("CHAT_MODEL", CHAT_MODEL), ("EMBED_MODEL", EMBED_MODEL)):
        add(name, "required" if value.strip() else "invalid", value or "(empty)")

    # TTS / STT
    add("TTS_MODEL", "required" if TTS_MODEL in _TTS_MODELS else "invalid", TTS_MODEL)
    for name, value in (("TTS_VOICE_A", TTS_VOICE_A), ("TTS_VOICE_B", TTS_VOICE_B)):
        add(name, "optional" if value.strip() else "invalid", value or "(empty)")
    stt_ok = (STT_MODEL.startswith("whisper-")
              and STT_MODEL.removeprefix("whisper-") in _STT_SIZES)
    add("STT_MODEL", "required" if stt_ok else "invalid",
        STT_MODEL if stt_ok
        else f"{STT_MODEL!r} (expected: whisper-<{'|'.join(_STT_SIZES)}>)")

    # MLX (optional chat backend)
    add("MLX_ENABLED",
        "optional" if MLX_MODE in _MLX_MODES else "invalid",
        MLX_MODE if MLX_MODE in _MLX_MODES
        else f"{MLX_MODE!r} (expected: auto|true|false; using auto)")
    add("MLX_BASE_URL",
        "optional" if _is_http(MLX_BASE_URL) else "invalid", MLX_BASE_URL)
    add("MLX_REQUEST_TIMEOUT",
        "invalid" if _invalid_float("MLX_REQUEST_TIMEOUT", 1.0)
        or MLX_REQUEST_TIMEOUT < 1.0 else "optional", str(MLX_REQUEST_TIMEOUT))

    # Server bind — single authority read by the launcher, uvicorn, and probes.
    host_raw = os.environ.get("HOST")
    host_bad = not HOST or (host_raw is not None and not host_raw.strip())
    add("HOST", "invalid" if host_bad else "optional", HOST)
    port_bad = _invalid_int("PORT", 1, 65535) or not 1 <= PORT <= 65535
    add("PORT", "invalid" if port_bad else "required", str(PORT))

    # Upload limits
    add("MAX_UPLOAD_MB",
        "invalid" if _invalid_int("MAX_UPLOAD_MB", 1) or MAX_UPLOAD_BYTES <= 0
        else "optional", str(MAX_UPLOAD_BYTES // (1024 * 1024)))
    add("MEDIA_MAX_UPLOAD_MB",
        "invalid" if _invalid_int("MEDIA_MAX_UPLOAD_MB", 1) or MEDIA_MAX_UPLOAD_BYTES <= 0
        else "optional", str(MEDIA_MAX_UPLOAD_BYTES // (1024 * 1024)))
    return report
