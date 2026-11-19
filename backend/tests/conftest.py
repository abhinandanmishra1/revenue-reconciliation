import os

# Test settings must exist before the app reads them.
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/revrec_test"))
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.setdefault("APP_ORIGINS", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("TRUSTED_PROXY_HOPS", "0")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("GEMINI_MODEL", "test-model")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

ORIGIN = "http://localhost:3000"
TABLES = "explanations, reconciliation_items, payments, orders, imports, users"


@pytest.fixture(scope="session")
def db_engine():
    from alembic import command
    from alembic.config import Config

    from app.db.session import get_engine

    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres is not reachable; set TEST_DATABASE_URL")
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return engine


@pytest.fixture
def db(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    yield db_engine


@pytest.fixture
def limits(monkeypatch):
    from app.security import ratelimit

    class FakeClock:
        now = 1000.0

        def __call__(self):
            return self.now

    clock, slept = FakeClock(), []
    fresh = ratelimit.Limits(clock=clock, sleep=slept.append)
    fresh.fake_clock, fresh.slept = clock, slept
    monkeypatch.setattr("app.routers.auth.limits", fresh)
    monkeypatch.setattr("app.security.ratelimit.limits", fresh)
    try:
        monkeypatch.setattr("app.routers.explain.limits", fresh)
    except (AttributeError, ImportError):
        pass
    return fresh


@pytest.fixture
def client(db, limits):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"Origin": ORIGIN}, raise_server_exceptions=False) as c:
        yield c


def signup(client, email="user@example.com", password="correct-horse"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r


@pytest.fixture
def user_client(client):
    signup(client)
    return client
