"""Authentication: first-run setup, login, session guard, throttling, sign-out."""
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
    assert anon_client.post("/api/auth/setup",
                            json={"password": "another-password"}).status_code == 409


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
        v for k, v in client.post("/api/auth/login",
                                  json={"password": TEST_PASSWORD}).headers.multi_items()
        if k.lower() == "set-cookie"
    )
    lowered = header.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered


def test_only_token_hash_is_persisted(client):
    token = client.cookies.get(auth.COOKIE_NAME)
    assert token and db.get_session(token) is None      # raw token is not a key
    assert auth.validate_session(token) is True         # but its hash resolves


def test_expired_session_rejected(client):
    from datetime import datetime, timedelta, timezone
    token = client.cookies.get(auth.COOKIE_NAME)
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db.create_session(auth._hash_token(token), past)
    assert auth.validate_session(token) is False
    assert client.get("/api/notebooks").status_code == 401
