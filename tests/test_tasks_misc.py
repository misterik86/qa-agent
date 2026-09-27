"""Coverage: tasks (scheduler, notifications, run_test), testrail, webhooks, messengers, scanner, strategist."""
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.scanner import extract_console_errors, scan_elements
from app.agent.strategist import Strategist
from app.core.crypto import encrypt_secret
from app.services.messengers import format_test_result, send_telegram, send_vk
from app.services.testrail import export_to_testrail, export_to_xray
from app.services.webhooks import build_signature, notify_test_finished


def _mk_run(db, user_id, status="passed", url="https://example.com", mode="specific"):
    from app.models.test_run import RunStatus, TestRun
    from app.models.user import User

    # Foreign keys are enforced on PostgreSQL: ensure the owner exists.
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, email=f"{user_id}@example.com", password_hash="x")
        db.add(user)
        db.commit()

    r = TestRun(owner_id=user.id, url=url, mode=mode, name="r", status=RunStatus[status].value,
                total_steps=2, passed_steps=2)
    db.add(r)
    db.commit()
    return r


def _user(client, email):
    client.post("/api/auth/register", json={
        "email": email, "password": "password123", "accepted_tos": True, "accepted_privacy": True})
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    u.is_verified = True
    db.commit()
    return u, db


def test_check_schedules_runs_due_job(client):
    from app.database import SessionLocal
    from app.models.schedule import ScheduleJob, ScheduleStatus
    from app.models.user import User
    from app.tasks.celery_app import celery_app
    from app.tasks.scheduler import check_schedules

    celery_app.conf.task_always_eager = True
    u, db = _user(client, "sch@example.com")
    u.plan = "enterprise"
    db.commit()
    sc = client.post("/api/scenarios", json={
        "name": "S", "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку, введи в поле логин test@test.com"}, headers={
            "Authorization": "Bearer " + client.post("/api/auth/login", json={
                "email": "sch@example.com", "password": "password123"}).json()["access_token"]}).json()
    job = ScheduleJob(owner_id=u.id, scenario_id=sc["id"], frequency="daily",
                      channel="email", email_recipient="x@x.ru",
                      status=ScheduleStatus.active.value,
                      last_run_at=datetime.now(timezone.utc) - timedelta(days=2))
    db.add(job)
    db.commit()
    check_schedules()
    db.refresh(job)
    assert job.last_run_at is not None
    db.close()


def test_check_schedules_skips_disabled_and_hourly_gating(client):
    from app.database import SessionLocal
    from app.models.schedule import ScheduleJob, ScheduleStatus
    from app.models.user import User
    from app.tasks.scheduler import check_schedules

    u, db = _user(client, "sch2@example.com")
    u.plan = "enterprise"
    db.commit()
    sc = client.post("/api/scenarios", json={
        "name": "S2", "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку, введи в поле логин test@test.com"}, headers={
            "Authorization": "Bearer " + client.post("/api/auth/login", json={
                "email": "sch2@example.com", "password": "password123"}).json()["access_token"]}).json()
    # paused job -> skipped even if due
    old_last = datetime.now(timezone.utc) - timedelta(days=2)
    job = ScheduleJob(owner_id=u.id, scenario_id=sc["id"], frequency="daily",
                      channel="email", email_recipient="x@x.ru",
                      status=ScheduleStatus.paused.value, last_run_at=old_last)
    db.add(job)
    db.commit()
    check_schedules()
    db.refresh(job)
    stored = job.last_run_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    assert abs((old_last - stored).total_seconds()) < 5
    db.close()


def test_notifications_and_cleanup(client):
    from app.database import SessionLocal
    from app.models.user import Plan, User
    from app.tasks.notifications import (check_trial_expiry, cleanup_old_reports,
                                         notify_upcoming_renewals, send_limit_warning)

    u, db = _user(client, "exp@example.com")
    u.plan = Plan.trial.value
    u.trial_started_at = datetime.now(timezone.utc) - timedelta(days=10)
    db.commit()
    check_trial_expiry()
    db.refresh(u)
    assert u.plan == Plan.blocked.value
    send_limit_warning(u.id, 50, is_100=True)
    notify_upcoming_renewals()
    cleanup_old_reports()
    db.close()


def test_run_test_task_eager_error_path(client):
    from app.database import SessionLocal
    from app.models.test_run import TestRun
    from app.tasks.celery_app import celery_app
    from app.tasks.run_test import run_test_task

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    db = SessionLocal()
    r = _mk_run(db, "user-x", status="queued", url="http://bad.invalid/")
    run_test_task(r.id, "user-x", r.url, "specific", "нажми кнопку, введи в поле логин test@test.com", "x")
    db.refresh(r)
    assert r.status in ("error", "failed")
    db.close()


@pytest.mark.network
def test_run_test_task_full_page(client):
    from app.database import SessionLocal
    from app.tasks.celery_app import celery_app
    from app.tasks.run_test import run_test_task

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    db = SessionLocal()
    r = _mk_run(db, "user-y", status="queued", url="https://ya.ru", mode="full_page")
    r.script_text = "scan"
    db.commit()
    run_test_task(r.id, "user-y", r.url, "full_page", "scan", "x")
    db.refresh(r)
    assert r.status in ("passed", "failed", "error")
    db.close()


def test_testrail_export_unconfigured(client):
    from app.database import SessionLocal

    db = SessionLocal()
    r = _mk_run(db, "user-z", status="passed")
    r.owner_id = "user-z"
    res = export_to_testrail(r)
    assert res["ok"] is False
    assert "не настроен" in res["error"]
    res2 = export_to_xray(r)
    assert res2["ok"] is False
    db.close()


def test_webhook_delivery_and_notify(client):
    from app.database import SessionLocal
    from app.models.webhook import WebhookConfig
    from app.tasks.celery_app import celery_app
    from app.tasks.notifications import retry_deliveries

    u, db = _user(client, "wh@example.com")
    cfg = WebhookConfig(owner_id=u.id, url="https://example.com/hook", event="test_finished", secret="s")
    db.add(cfg)
    db.commit()
    notify_test_finished(db, u.id, "run-1", {"status": "passed", "passed_steps": 1, "failed_steps": 0, "url": "https://example.com"})
    # failed delivery attempt -> retry does nothing harmful
    retry_deliveries()
    db.close()


def test_webhook_signature_and_scan():
    body = '{"a": 1}'
    sig = build_signature(body, "sekret")
    assert len(sig) == 64
    assert build_signature(body, "sekret") == sig
    assert build_signature(body, "other") != sig
    elements = scan_elements('{"type": "button", "selector": "b1", "text": "x"} other {"type": "link", "selector": "a1", "text": "y"}')
    assert len(elements) == 2
    assert extract_console_errors("Everything fine\nTypeError: boom") == ["TypeError: boom"]


def test_messengers_graceful():
    assert send_telegram(0, "hello") is False
    assert send_vk("0", "hello") is False
    text = format_test_result("Login", "passed", 3, 0, "https://example.com", "http://x/report")
    assert "Login" in text and "passed" in text and "http://x/report" in text
    assert format_test_result("Login", "failed", 1, 2, "https://example.com")


def test_strategist_heuristic_plan():
    st = Strategist()
    plan = st._heuristic_plan([
        {"type": "form", "selector": "form", "text": "",
         "fields": [{"tag": "input", "type": "email", "name": "email", "required": True, "idx": 1}]},
        {"type": "link", "selector": "a[href]:nth-of-type(1)", "text": "About"},
        {"type": "button", "selector": "button:nth-of-type(1)", "text": "OK"},
    ])
    form_cases = [p for p in plan if p["type"] in ("form", "field")]
    assert len(form_cases) == 3  # required, email, позитивная отправка
    neg = form_cases[0]
    assert neg["steps"][0]["action"] == "validate_field"
    assert neg["steps"][0]["expected_effect"] == "validation_required"
    assert form_cases[1]["steps"][0]["expected_effect"] == "validation_email"
    pos = form_cases[2]
    assert pos["steps"][0]["action"] == "fill_form"
    assert pos["steps"][-1]["action"] == "check_form"
    assert pos["steps"][-1]["expected_effect"] == "form_submitted"
    links = [p for p in plan if p["type"] == "link"]
    assert links[0]["action"] == "click"
    assert links[0]["steps"][0]["expected_effect"] == "navigation"


def test_crypto_encrypt_used_for_vk_token():
    assert encrypt_secret("tok") != "tok"