"""Installation diagnostics: `python -m app.diagnostics` (or `uv run python -m app.diagnostics`).

Checks every local dependency the app needs and prints actionable failures.
Exit code 0 = all good, 1 = at least one check failed.
"""
import sys

from . import db
from .config import (
    APP_VERSION,
    AUDIO_DIR,
    CHAT_MODEL,
    DATA_DIR,
    EMBED_MODEL,
    LANCEDB_DIR,
    OLLAMA_BASE_URL,
    PRODUCT_NAME,
    SQLITE_PATH,
    TTS_MODEL,
    UPLOADS_DIR,
)
from .config import STT_MODEL  # noqa: E402  (grouped with config imports above)
from .providers import get_llm, get_stt, get_tts

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def check(name: str, ok: bool, detail: str, fix: str | None = None) -> bool:
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}: {detail}")
    if not ok and fix:
        print(f"         {YELLOW}fix:{RESET} {fix}")
    return ok


def main() -> int:
    print(f"{PRODUCT_NAME} v{APP_VERSION} — diagnostics\n")
    ok = True

    print("Storage")
    for label, path in (("data dir", DATA_DIR), ("uploads", UPLOADS_DIR),
                        ("vector store", LANCEDB_DIR), ("audio", AUDIO_DIR)):
        writable = path.exists() and path.is_dir()
        ok &= check(label, writable, str(path),
                    f"create the directory: mkdir -p {path}")
    try:
        db.init_db()
        counts = db.counts()
        ok &= check("sqlite", True,
                    f"{SQLITE_PATH} ({counts['notebooks']} notebooks, {counts['sources']} sources)")
    except Exception as e:
        ok &= check("sqlite", False, f"{SQLITE_PATH}: {e}",
                    "delete the corrupt DB file to start fresh (loses notebook metadata)")

    print("\nLLM backend")
    llm = get_llm().status()
    ok &= check("ollama", llm.get("reachable", False), OLLAMA_BASE_URL,
                "start Ollama (`ollama serve` or launch the app), or fix OLLAMA_BASE_URL in .env")
    ok &= check(f"chat model ({CHAT_MODEL})", llm.get("chat_model_ready", False),
                "installed" if llm.get("chat_model_ready") else "not installed",
                f"ollama pull {CHAT_MODEL} (the app also auto-pulls at startup)")
    ok &= check(f"embed model ({EMBED_MODEL})", llm.get("embed_model_ready", False),
                "installed" if llm.get("embed_model_ready") else "not installed",
                f"ollama pull {EMBED_MODEL} (the app also auto-pulls at startup)")

    print("\nTTS backend")
    tts = get_tts().status()
    if TTS_MODEL == "kokoro" and not tts.get("ready"):
        # not fatal: kokoro weights auto-download on first Audio Overview
        print(f"  [{YELLOW}WARN{RESET}] tts (kokoro): {tts.get('detail', '')}")
    else:
        ok &= check(f"tts ({TTS_MODEL})", tts.get("ready", False), tts.get("detail", ""),
                    "set TTS_MODEL=kokoro (portable) or TTS_MODEL=say (macOS) in .env")

    mlx = llm.get("mlx")
    if mlx:
        print("\nMLX backend (optional)")
        if mlx.get("reachable"):
            check("mlx endpoint", True, f"{mlx.get('base_url')} — {mlx.get('detail')}")
        else:
            # Optional backend: an offline MLX endpoint must not fail diagnostics
            # (or block startup) — the app simply runs on Ollama until it appears.
            print(f"  [{YELLOW}WARN{RESET}] mlx endpoint: "
                  f"{mlx.get('base_url')} — {mlx.get('detail')}")
            print(f"         start it with: mlx_lm.server --host 127.0.0.1 --port 8080, "
                  f"or set MLX_ENABLED=false in .env")

    print("\nSTT backend (video/audio transcription)")
    stt = get_stt().status()
    if "download" in stt.get("detail", ""):
        print(f"  [{YELLOW}WARN{RESET}] stt ({STT_MODEL}): {stt['detail']}")
    else:
        ok &= check(f"stt ({STT_MODEL})", stt.get("ready", False), stt.get("detail", ""),
                    "set STT_MODEL=whisper-<tiny|base|small|medium|large-v3> in .env")

    print()
    if ok:
        print(f"{GREEN}All checks passed.{RESET}")
        return 0
    print(f"{RED}Some checks failed — see fixes above.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
