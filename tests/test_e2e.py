"""E2E: register -> login -> create scenario -> validate -> run (eager) -> report.
Uses Celery in eager mode so the task runs synchronously, and the local sandbox."""
import os

import pytest

os.environ.setdefault("SANDBOX_RUNNER", "local")


def _verify_user(email: str):
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_verified = True
        db.commit()
    finally:
        db.close()


@pytest.mark.network
def test_full_flow_e2e(client):
    from app.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    # 1. register
    r = client.post("/api/auth/register", json={
        "email": "e2e@example.com", "password": "password123",
        "full_name": "E2E", "accepted_tos": True, "accepted_privacy": True})
    assert r.status_code == 201
    _verify_user("e2e@example.com")

    # 2. login
    login = client.post("/api/auth/login", json={
        "email": "e2e@example.com", "password": "password123"}).json()
    h = {"Authorization": f"Bearer {login['access_token']}"}

    # 3. validate scenario
    valid = client.post("/api/scenarios/validate", json={
        "script_text": 'Нажми на кнопку "Войти", введи в поле логин test@test.com, введи в поле пароль 123456, нажми "Отправить", проверь, что появилось сообщение "Добро пожаловать"'
    }, headers=h).json()
    assert valid["valid"] is True

    # 4. create scenario
    sc = client.post("/api/scenarios", json={
        "name": "Login", "url": "https://example.com", "mode": "specific",
        "script_text": 'Нажми на кнопку "Войти", введи в поле логин test@test.com',
        "tags": ["smoke"]}, headers=h).json()
    assert sc["id"]

    # 5. run (against a public reachable site)
    run = client.post("/api/runs", json={
        "scenario_id": sc["id"], "consent": True, "url": "https://ya.ru",
        "name": "E2E run"}, headers=h)
    assert run.status_code == 201
    run_id = run.json()["id"]

    # 6. status should be finished (eager task)
    detail = client.get(f"/api/runs/{run_id}", headers=h).json()
    assert detail["status"] in ("passed", "failed", "error")
    assert detail["total_steps"] > 0

    # 7. reports in 4 formats
    for fmt in ("json", "html", "pdf", "junit"):
        rep = client.get(f"/api/runs/{run_id}/report/{fmt}", headers=h)
        assert rep.status_code == 200
        assert len(rep.content) > 0

    # 8. dashboard stats
    stats = client.get("/api/dashboard/stats", headers=h).json()
    assert stats["total_runs"] >= 1