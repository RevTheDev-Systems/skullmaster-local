"""`python -m app` — start the server using the configured HOST/PORT.

This is the single entry point the launcher, the macOS app, and the dev
tooling all use, so the bind address comes from `app.config` (which reads
`.env`) instead of being repeated as hardcoded flags.
"""

import logging

import uvicorn

from .config import HOST, PORT
from .main import app

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def main() -> None:
    if HOST not in LOOPBACK:
        # Local-first promise: loopback is the default. If the operator binds
        # elsewhere, the session password is the only access control.
        logging.getLogger("skullmaster").warning(
            "HOST=%s is not loopback — the app is reachable on the network and "
            "the owner password is the only guard. Bind 127.0.0.1 unless you "
            "trust the network.",
            HOST,
        )
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
