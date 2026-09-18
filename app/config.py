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
MLX_REQUEST_TIMEOUT = float(os.environ.get("MLX_REQUEST_TIMEOUT", "30"))
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

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8501"))

# ---- Retrieval tuning ----
CHUNK_CHARS = 3200        # ~800 tokens
CHUNK_OVERLAP = 400       # ~100 tokens
TOP_K = 8                 # excerpts handed to the model per question
VECTOR_CANDIDATES = 24    # candidates fetched from each retriever before fusion
CONTEXT_HISTORY_TURNS = 4 # prior chat turns included for conversational context

# ---- Podcast ----
PODCAST_CONTEXT_CHARS = 24_000  # max source characters fed to the script writer

# ---- Ingestion safety limits ----
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
MEDIA_MAX_UPLOAD_BYTES = int(os.environ.get("MEDIA_MAX_UPLOAD_MB", "1024")) * 1024 * 1024
URL_FETCH_TIMEOUT = 20          # seconds
URL_MAX_BYTES = 10 * 1024 * 1024

# ---- Studio artifacts ----
ARTIFACT_CONTEXT_CHARS = 24_000  # max source characters fed to artifact generation


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
