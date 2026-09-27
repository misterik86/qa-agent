"""Test fixtures: SQLite in-memory DB, test client, test user."""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_run.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("SANDBOX_RUNNER", "local")
# Unit tests must never hit the real AI providers (a configured API key makes
# Strategist.generate_plan do real network calls and hang). Fail fast so the
# deterministic heuristic fallback kicks in: fast, offline, deterministic.
os.environ["AI_TIMEOUT_SECONDS"] = "1"
os.environ["AI_RETRIES"] = "0"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine, SessionLocal, get_db
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def clean_db(setup_db):
    """Truncate all tables before each test for isolation."""
    from app.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    from app.database import Base as _Base

    db = SessionLocal()
    try:
        for table in reversed(_Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def test_user(client):
    """Register + verify (via DB) + return credentials."""
    resp = client.post("/api/auth/register", json={
        "email": "test@example.com",
        "password": "password123",
        "full_name": "Test User",
        "accepted_tos": True,
        "accepted_privacy": True,
    })
    assert resp.status_code == 201
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "test@example.com").first()
        user.is_verified = True
        db.commit()
    finally:
        db.close()
    return {"email": "test@example.com", "password": "password123"}


@pytest.fixture
def auth_headers(client, test_user):
    login = client.post("/api/auth/login", json={
        "email": test_user["email"], "password": test_user["password"]})
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}