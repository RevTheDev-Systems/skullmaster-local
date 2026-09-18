"""Authoritative local health report: `python -m app.diagnostics`.

Checks Python, dependencies, configuration, storage (with a real write test),
SQLite, LanceDB, the LLM/TTS/STT backends, disk capacity, and whether the API
server is listening. Each line is PASS / WARN / FAIL; WARN means optional or
recoverable, FAIL means something required is broken.

Exit code 0 = no FAIL, 1 = at least one FAIL.
"""

import importlib
import shutil
import socket
import sys
import tempfile

from . import db, store
from .config import (
    APP_VERSION,
    AUDIO_DIR,
    CHAT_MODEL,
    DATA_DIR,
    EMBED_MODEL,
    HOST,
    LANCEDB_DIR,
    OLLAMA_BASE_URL,
    PORT,
    PRODUCT_NAME,
    SQLITE_PATH,
    STT_MODEL,
    TTS_MODEL,
    UPLOADS_DIR,
    config_report,
)
from .providers import get_llm, get_stt, get_tts

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

# (import name, distribution name) — checked without requiring a live model.
DEPENDENCIES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("ollama", "ollama"),
    ("lancedb", "lancedb"),
    ("fitz", "pymupdf"),
    ("trafilatura", "trafilatura"),
    ("docx", "python-docx"),
    ("openpyxl", "openpyxl"),
    ("numpy", "numpy"),
    ("faster_whisper", "faster-whisper"),
    ("kokoro_onnx", "kokoro-onnx"),
]


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print(f"{PRODUCT_NAME} v{APP_VERSION} — diagnostics\n")
    tally = {"fail": 0, "warn": 0}

    def check(name: str, ok: bool, detail: str, fix: str | None = None) -> bool:
        if ok:
            print(f"  [{GREEN}PASS{RESET}] {name}: {detail}")
        else:
            tally["fail"] += 1
            print(f"  [{RED}FAIL{RESET}] {name}: {detail}")
            if fix:
                print(f"         {YELLOW}fix:{RESET} {fix}")
        return ok

    def warn(name: str, detail: str, fix: str | None = None) -> None:
        tally["warn"] += 1
        print(f"  [{YELLOW}WARN{RESET}] {name}: {detail}")
        if fix:
            print(f"         {YELLOW}fix:{RESET} {fix}")

    print("Runtime")
    version = sys.version_info
    check(
        "python",
        version >= (3, 12),
        f"{version.major}.{version.minor}.{version.micro}",
        "SkullMaster requires Python 3.12+",
    )

    missing = []
    for module, dist in DEPENDENCIES:
        try:
            importlib.import_module(module)
        except Exception:
            missing.append(dist)
    check(
        "dependencies",
        not missing,
        "all present" if not missing else f"missing: {', '.join(missing)}",
        "run: uv sync",
    )

    print("\nConfiguration")
    for item in config_report():
        if item["status"] == "invalid":
            check(item["name"], False, item["detail"], "fix or remove this value in .env")
        else:
            check(f"{item['name']} ({item['status']})", True, item["detail"])

    print("\nStorage")
    for label, path in (
        ("data dir", DATA_DIR),
        ("uploads", UPLOADS_DIR),
        ("vector store", LANCEDB_DIR),
        ("audio", AUDIO_DIR),
    ):
        writable = path.exists() and path.is_dir()
        check(label, writable, str(path), f"create the directory: mkdir -p {path}")
    try:
        with tempfile.NamedTemporaryFile(dir=DATA_DIR, delete=True) as fh:
            fh.write(b"ok")
            fh.flush()
        check("data writable", True, f"wrote and removed a test file in {DATA_DIR}")
    except Exception as e:
        check(
            "data writable",
            False,
            f"{DATA_DIR}: {e}",
            "check permissions and ownership of the data directory",
        )
    try:
        free_gb = shutil.disk_usage(DATA_DIR).free / (1024**3)
        if free_gb < 1.0:
            warn("disk space", f"only {free_gb:.2f} GB free under {DATA_DIR}")
        else:
            check("disk space", True, f"{free_gb:.1f} GB free")
    except Exception as e:
        warn("disk space", str(e))

    try:
        db.init_db()
        counts = db.counts()
        check(
            "sqlite",
            True,
            f"{SQLITE_PATH} ({counts['notebooks']} notebooks, {counts['sources']} sources)",
        )
    except Exception as e:
        check(
            "sqlite",
            False,
            f"{SQLITE_PATH}: {e}",
            "delete the corrupt DB file to start fresh (loses notebook metadata)",
        )

    try:
        vector = store.status()
        check(
            "lancedb",
            vector.get("ready", False),
            f"{vector.get('path')} ({vector.get('chunks', 0)} chunks)",
        )
    except Exception as e:
        check("lancedb", False, str(e), "check permissions on the data directory")

    print("\nLLM backend")
    llm = get_llm().status()
    check(
        "ollama",
        llm.get("reachable", False),
        OLLAMA_BASE_URL,
        "start Ollama (`ollama serve` or launch the app), or fix OLLAMA_BASE_URL in .env",
    )
    check(
        f"chat model ({CHAT_MODEL})",
        llm.get("chat_model_ready", False),
        "installed" if llm.get("chat_model_ready") else "not installed",
        f"ollama pull {CHAT_MODEL} (the app also auto-pulls at startup)",
    )
    check(
        f"embed model ({EMBED_MODEL})",
        llm.get("embed_model_ready", False),
        "installed" if llm.get("embed_model_ready") else "not installed",
        f"ollama pull {EMBED_MODEL} (the app also auto-pulls at startup)",
    )
    for backend, state in (llm.get("provider_states") or {}).items():
        print(f"  [{GREEN}PASS{RESET}] provider {backend}: {state.get('state')}")

    mlx = llm.get("mlx")
    if mlx:
        print("\nMLX backend (optional)")
        if mlx.get("reachable"):
            check("mlx endpoint", True, f"{mlx.get('base_url')} — {mlx.get('detail')}")
        else:
            # Optional backend: an offline MLX endpoint must not fail diagnostics
            # (or block startup) — the app simply runs on Ollama until it appears.
            warn(
                "mlx endpoint",
                f"{mlx.get('base_url')} — {mlx.get('detail')}",
                "start it with: mlx_lm.server --host 127.0.0.1 --port 8080, "
                "or set MLX_ENABLED=false in .env",
            )

    print("\nTTS backend")
    tts = get_tts().status()
    if TTS_MODEL == "kokoro" and not tts.get("ready"):
        warn("tts (kokoro)", tts.get("detail", ""))
    else:
        check(
            f"tts ({TTS_MODEL})",
            tts.get("ready", False),
            tts.get("detail", ""),
            "set TTS_MODEL=kokoro (portable) or TTS_MODEL=say (macOS) in .env",
        )

    print("\nSTT backend (video/audio transcription)")
    stt = get_stt().status()
    if "download" in stt.get("detail", ""):
        warn(f"stt ({STT_MODEL})", stt["detail"])
    else:
        check(
            f"stt ({STT_MODEL})",
            stt.get("ready", False),
            stt.get("detail", ""),
            "set STT_MODEL=whisper-<tiny|base|small|medium|large-v3> in .env",
        )

    print("\nServer")
    open_host = "127.0.0.1" if HOST in ("0.0.0.0", "::", "") else HOST
    url = f"http://{open_host}:{PORT}"
    if _port_open(HOST, PORT):
        check("api server", True, f"listening on {url}")
    else:
        warn(
            "api server",
            f"not running on {url}",
            "start it with: uv run python -m app (or the launcher)",
        )

    print()
    if tally["fail"]:
        suffix = f", {tally['warn']} warning(s)" if tally["warn"] else ""
        print(f"{RED}{tally['fail']} check(s) failed{RESET}{suffix}.")
        return 1
    if tally["warn"]:
        print(f"{YELLOW}All required checks passed, {tally['warn']} warning(s).{RESET}")
        return 0
    print(f"{GREEN}All checks passed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
