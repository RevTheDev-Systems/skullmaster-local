"""`python -m app` — start the server using the configured HOST/PORT.

This is the single entry point the launcher, the macOS app, and the dev
tooling all use, so the bind address comes from `app.config` (which reads
`.env`) instead of being repeated as hardcoded flags.
"""
import uvicorn

from .config import HOST, PORT
from .main import app


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
