"""Authentication: first-run setup, login, session guard, throttling, sign-out."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from app import auth, db
from tests.conftest import TEST_PASSWORD


@pytest.fixture(autouse=True)
def _reset_throttle():
    auth._failures.clear()
    yield
    auth._failures.clear()


@pytest.fixture()
def virgin_install():
    """Wipe the owner account so a first-run flow can be exercised.

    The suite shares one temp data dir, so tests that assert on setup state have
    to clear it explicitly rather than relying on execution order.
    """
    with db.conn() as c:
        c.execute("DELETE FROM app_user")
        c.execute("DELETE FROM sessions")
    yield


# ---------- guard ----------


def test_api_requires_session(anon_client):
    for path in ("/api/notebooks", "/health", "/api/notebooks/x/sources"):
        res = anon_client.get(path)
        assert res.status_code == 401, path
        assert res.json()["detail"] == "Authentication required"


def test_public_endpoints_open(anon_client):
    assert anon_client.get("/healthz").json()["status"] == "alive"
    assert anon_client.get("/api/auth/status").status_code == 200
    assert anon_client.get("/static/login.js").status_code == 200


def test_html_navigation_redirects_to_login(anon_client):
    res = anon_client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"


def test_login_page_served(anon_client):
    body = anon_client.get("/login").text
    assert "Sign in" in body and "login.js" in body


# ---------- setup + login ----------


def test_setup_then_login_flow(anon_client, virgin_install):
    assert anon_client.get("/api/auth/status").json()["setup_required"] is True

    short = anon_client.post("/api/auth/setup", json={"password": "abc"})
    assert short.status_code == 400
    assert "at least" in short.json()["detail"]

    ok = anon_client.post("/api/auth/setup", json={"password": TEST_PASSWORD})
    assert ok.status_code == 200
    assert auth.COOKIE_NAME in ok.cookies or anon_client.cookies.get(auth.COOKIE_NAME)

    status = anon_client.get("/api/auth/status").json()
    assert status["authenticated"] is True and status["setup_required"] is False
    assert anon_client.get("/api/notebooks").status_code == 200

    # setup cannot be replayed to take over an existing install
    assert (
        anon_client.post("/api/auth/setup", json={"password": "another-password"}).status_code
        == 409
    )


def test_wrong_password_rejected_and_password_not_stored(client):
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"password": "wrong-one"}).status_code == 401
    assert client.get("/api/notebooks").status_code == 401

    stored = db.get_app_user()
    assert TEST_PASSWORD not in stored["password_hash"]
    assert stored["password_hash"] != TEST_PASSWORD
    assert stored["iterations"] >= 1000 and len(stored["salt"]) == 32


def test_login_throttled_after_repeated_failures(client):
    client.cookies.clear()
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    locked = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert locked.status_code == 429 and "try again" in locked.json()["detail"]


def test_logout_invalidates_session(client):
    assert client.get("/api/notebooks").status_code == 200
    token = client.cookies.get(auth.COOKIE_NAME)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/notebooks").status_code == 401

    # the old token is dead server-side, not merely dropped by the browser
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get("/api/notebooks").status_code == 401


def test_session_cookie_hardening(client):
    header = "".join(
        v
        for k, v in client.post(
            "/api/auth/login", json={"password": TEST_PASSWORD}
        ).headers.multi_items()
        if k.lower() == "set-cookie"
    )
    lowered = header.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered


def test_only_token_hash_is_persisted(client):
    token = client.cookies.get(auth.COOKIE_NAME)
    assert token and db.get_session(token) is None  # raw token is not a key
    assert auth.validate_session(token) is True  # but its hash resolves


def test_expired_session_rejected(client):
    token = client.cookies.get(auth.COOKIE_NAME)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.create_session(auth._hash_token(token), past)
    assert auth.validate_session(token) is False
    assert client.get("/api/notebooks").status_code == 401


# ---------- session expiry: timestamps, not strings ----------


def test_session_expiry_compares_aware_datetimes(client):
    """Regression: ISO strings compared lexicographically mis-order timestamps
    that differ only by microseconds."""
    now = datetime.now(timezone.utc)
    for delta, expected in [
        (timedelta(seconds=1), True),
        (timedelta(microseconds=-1), False),
        (timedelta(0), False),
    ]:
        token = f"boundary-{delta}"
        db.create_session(auth._hash_token(token), (now + delta).isoformat())
        assert auth.validate_session(token) is expected, delta


def test_naive_session_timestamp_is_treated_as_utc(client):
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    token = "naive-future"
    db.create_session(auth._hash_token(token), future.isoformat())
    assert auth.validate_session(token) is True


def test_malformed_session_timestamp_fails_closed(client):
    token = "malformed"
    db.create_session(auth._hash_token(token), "not-a-timestamp")
    assert auth.validate_session(token) is False
    assert db.get_session(auth._hash_token(token)) is None  # dead row removed


def test_purge_removes_expired_and_malformed_only(client):
    now = datetime.now(timezone.utc)
    db.create_session(auth._hash_token("expired"), (now - timedelta(seconds=1)).isoformat())
    db.create_session(auth._hash_token("malformed"), "garbage")
    db.create_session(auth._hash_token("valid"), (now + timedelta(days=1)).isoformat())
    db.purge_expired_sessions(now)
    assert db.get_session(auth._hash_token("expired")) is None
    assert db.get_session(auth._hash_token("malformed")) is None
    assert db.get_session(auth._hash_token("valid")) is not None


def test_lockout_expires(client):
    client.cookies.clear()
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 429

    key = next(iter(auth._failures))  # request.client.host
    auth._failures[key] = (0, time.time() - 1)  # lockout window elapsed
    assert auth.seconds_until_unlocked(key) == 0
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 200


def test_session_survives_server_restart(client):
    from fastapi.testclient import TestClient

    from app import main as main_mod

    token = client.cookies.get(auth.COOKIE_NAME)
    assert token
    with TestClient(main_mod.app) as fresh:
        fresh.cookies.set(auth.COOKIE_NAME, token)
        assert fresh.get("/api/notebooks").status_code == 200


def test_stale_cookie_is_rejected(client):
    client.cookies.set(auth.COOKIE_NAME, "stale-not-a-real-token")
    assert client.get("/api/notebooks").status_code == 401


def test_login_page_offers_create_and_return_to_logon(anon_client):
    html = anon_client.get("/login").text
    assert 'id="login-alt"' in html  # the mode-switch button exists
    js = anon_client.get("/static/login.js").text
    assert "Create account" in js
    assert "Return to logon" in js
    assert "applyLoginMode" in js and "applySetupMode" in js
