import pytest

from app.config import get_settings
from app.main import app
from tests.conftest import ORIGIN, signup


def test_signup_starts_a_session_with_safe_cookie_flags(client):
    r = signup(client, email="  New.User@Example.com ")
    assert r.json()["user"]["email"] == "new.user@example.com"
    cookie = r.headers["set-cookie"].lower()
    assert "session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert client.get("/api/auth/me").status_code == 200


def test_cookie_is_secure_when_configured(client):
    secure = get_settings().model_copy(update={"cookie_secure": True})
    app.dependency_overrides[get_settings] = lambda: secure
    try:
        r = client.post("/api/auth/signup", json={"email": "s@example.com", "password": "password1"})
    finally:
        app.dependency_overrides.clear()
    assert "secure" in r.headers["set-cookie"].lower()


def test_duplicate_signup_is_409_regardless_of_case(client):
    signup(client, email="dup@example.com")
    r = client.post("/api/auth/signup", json={"email": "DUP@example.com", "password": "another-pass"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize("password", ["short", "x" * 73, "é" * 37])
def test_password_must_be_8_to_72_bytes(client, password):
    r = client.post("/api/auth/signup", json={"email": "p@example.com", "password": password})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_invalid_email_is_422(client):
    assert client.post("/api/auth/signup", json={"email": "nope", "password": "password1"}).status_code == 422


def test_login_logout_and_me(client):
    signup(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "USER@example.com", "password": "correct-horse"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["email"] == "user@example.com"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_unknown_email_and_wrong_password_look_the_same(client):
    signup(client)
    client.cookies.clear()
    a = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong-pass"})
    b = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "wrong-pass"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_overlong_login_password_is_401_not_500(client):
    signup(client)
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "x" * 100})
    assert r.status_code == 401


def test_tampered_or_missing_session_is_401(client):
    assert client.get("/api/imports").status_code == 401
    client.cookies.set("session", "not-a-jwt")
    r = client.get("/api/imports")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


# ------------------------------------------------------------------ origin checks


def test_foreign_origin_is_403(client):
    r = client.post("/api/auth/signup", json={"email": "o@example.com", "password": "password1"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"


def test_referer_is_used_when_origin_is_missing(client):
    client.headers.pop("Origin")
    ok = client.post("/api/auth/signup", json={"email": "r@example.com", "password": "password1"},
                     headers={"Referer": f"{ORIGIN}/signup"})
    bad = client.post("/api/auth/signup", json={"email": "r2@example.com", "password": "password1"},
                      headers={"Referer": "https://evil.example/page"})
    assert ok.status_code == 201 and bad.status_code == 403


def test_missing_origin_and_referer_is_403(client):
    client.headers.pop("Origin")
    r = client.post("/api/auth/login", json={"email": "x@example.com", "password": "password1"})
    assert r.status_code == 403


def test_get_requests_do_not_need_origin(client):
    client.headers.pop("Origin")
    assert client.get("/api/health").status_code == 200


# ------------------------------------------------------------------ rate limits


def _login(client, password="wrong-pass", email="user@example.com", ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


@pytest.fixture
def behind_proxy():
    settings = get_settings().model_copy(update={"trusted_proxy_hops": 1})
    app.dependency_overrides[get_settings] = lambda: settings
    yield
    app.dependency_overrides.clear()


def test_five_failures_per_ip_and_email_then_429_but_other_ip_allowed(client, behind_proxy):
    signup(client)
    for _ in range(5):
        assert _login(client, ip="1.1.1.1").status_code == 401
    blocked = _login(client, ip="1.1.1.1", password="correct-horse")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert _login(client, ip="2.2.2.2", password="correct-horse").status_code == 200


def test_forged_forwarded_entries_are_ignored(client, behind_proxy):
    signup(client)
    for i in range(5):
        _login(client, ip=f"9.9.9.{i}, 1.1.1.1")
    assert _login(client, ip="8.8.8.8, 1.1.1.1").status_code == 429


def test_success_resets_the_failure_counter(client, behind_proxy):
    signup(client)
    for _ in range(4):
        _login(client, ip="1.1.1.1")
    assert _login(client, ip="1.1.1.1", password="correct-horse").status_code == 200
    for _ in range(4):
        assert _login(client, ip="1.1.1.1").status_code == 401


def test_failure_window_expires(client, behind_proxy, limits):
    signup(client)
    for _ in range(5):
        _login(client, ip="1.1.1.1")
    limits.fake_clock.now += 15 * 60
    assert _login(client, ip="1.1.1.1", password="correct-horse").status_code == 200


def test_auth_ip_limit_is_20_per_minute(client, behind_proxy):
    codes = [_login(client, email=f"u{i}@example.com", ip="3.3.3.3").status_code for i in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


def test_per_email_slowdown_never_refuses_a_correct_password(client, behind_proxy, limits):
    signup(client)
    for i in range(12):  # 12 failures spread across IPs: no IP+email block
        _login(client, ip=f"4.4.4.{i}")
    r = _login(client, ip="5.5.5.5", password="correct-horse")
    assert r.status_code == 200
    assert limits.slept[-1] == 4.0  # 12 failures -> 2^(12-10) seconds


def test_known_and_unknown_emails_are_throttled_the_same(client, behind_proxy):
    signup(client)
    known = [_login(client, ip="6.6.6.6").status_code for _ in range(6)]
    unknown = [_login(client, email="ghost@example.com", ip="6.6.6.7").status_code for _ in range(6)]
    assert known == unknown == [401] * 5 + [429]
