"""Local single-user authentication: password setup, sessions, and login throttling.

Design notes:
- The password is never stored — only a PBKDF2-HMAC-SHA256 hash with a per-install
  random salt. The iteration count is stored alongside the hash so it can be raised
  later without invalidating existing passwords.
- Sessions live server-side in SQLite. The cookie holds a random token; the database
  stores only its SHA-256 hash, so a leaked database cannot be replayed as a login.
- There is no default or fallback password. Until the owner sets one on first run,
  the app reports `setup_required` and refuses to authenticate anything.
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from . import db

COOKIE_NAME = "sm_session"
SESSION_DAYS = 14
MIN_PASSWORD_LENGTH = 8

# OWASP-recommended work factor; lowered only by the test suite for speed.
#
# Review (local/offline threat model, Phase 4): 600k PBKDF2-HMAC-SHA256 is a
# deliberate, current work factor. The only attacker who benefits from raising
# it is one who already holds the database file — i.e. one who already has this
# machine's filesystem — so the incremental gain is small, while changing the
# derivation would invalidate every existing password. Keep as-is; the count is
# stored per hash, so it can be raised later without a forced migration.
PBKDF2_ITERATIONS = int(os.environ.get("SM_PBKDF2_ITERATIONS", "600000"))

# Login throttling (per process — this is a single-user localhost app).
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 60
_failures: dict[str, tuple[int, float]] = {}


class AuthError(Exception):
    """Raised for recoverable auth problems that are safe to show the user."""


# ---------- password hashing ----------

def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def setup_required() -> bool:
    return db.get_app_user() is None


def set_password(password: str):
    """Create (or replace) the owner password. Invalidates all existing sessions."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, PBKDF2_ITERATIONS)
    db.set_app_user(digest.hex(), salt.hex(), PBKDF2_ITERATIONS)
    db.delete_all_sessions()


def verify_password(password: str) -> bool:
    user = db.get_app_user()
    if not user:
        return False
    expected = bytes.fromhex(user["password_hash"])
    actual = _derive(password, bytes.fromhex(user["salt"]), user["iterations"])
    return hmac.compare_digest(expected, actual)


# ---------- throttling ----------

def _throttle_key(client: str) -> str:
    return client or "unknown"


def seconds_until_unlocked(client: str) -> int:
    count, until = _failures.get(_throttle_key(client), (0, 0.0))
    remaining = until - time.time()
    return int(remaining) + 1 if remaining > 0 else 0


def record_failure(client: str):
    key = _throttle_key(client)
    count, _ = _failures.get(key, (0, 0.0))
    count += 1
    until = time.time() + LOCKOUT_SECONDS if count >= MAX_FAILED_ATTEMPTS else 0.0
    _failures[key] = (0 if until else count, until)


def clear_failures(client: str):
    _failures.pop(_throttle_key(client), None)


# ---------- sessions ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session() -> str:
    """Issue a new session token (the raw value is returned only once, for the cookie)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    db.create_session(_hash_token(token), expires.isoformat())
    db.purge_expired_sessions(now)
    return token


def validate_session(token: str | None) -> bool:
    if not token:
        return False
    row = db.get_session(_hash_token(token))
    if not row:
        return False
    expires = db.parse_timestamp(row["expires_at"])
    # Compare timezone-aware datetimes, not ISO strings: lexicographic order is
    # wrong when two timestamps differ only in the presence of microseconds.
    # A malformed timestamp fails closed and its dead row is removed.
    if expires is None or expires <= datetime.now(timezone.utc):
        db.delete_session(row["token_hash"])
        return False
    return True


def destroy_session(token: str | None):
    if token:
        db.delete_session(_hash_token(token))
