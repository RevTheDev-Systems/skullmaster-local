"""Runtime configuration. All model/backend selection comes from .env — nothing hardcoded in app logic."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---- Paths ----
DATA_DIR = Path(os.environ.get("NLM_DATA_DIR", PROJECT_ROOT / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
LANCEDB_DIR = DATA_DIR / "lancedb"
AUDIO_DIR = DATA_DIR / "audio"
MODELS_DIR = PROJECT_ROOT / "models"      # local TTS weights
SQLITE_PATH = DATA_DIR / "notebooks.db"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

for d in (DATA_DIR, UPLOADS_DIR, LANCEDB_DIR, AUDIO_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---- Providers & models (env-only; see .env.example) ----
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "qwen3:30b")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
TTS_MODEL = os.environ.get("TTS_MODEL", "kokoro")
TTS_VOICE_A = os.environ.get("TTS_VOICE_A", "af_heart")
TTS_VOICE_B = os.environ.get("TTS_VOICE_B", "am_michael")

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


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
