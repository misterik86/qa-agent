"""Coverage for services: email, payments, messengers, webhooks, testrail, time prediction."""
from app.core.crypto import decrypt_secret, encrypt_secret, mask_email
from app.services.payments import PLAN_PRICES, create_payment, verify_webhook_signature
from app.services.messengers import format_test_result
from app.services.webhooks import build_signature
from app.services.time_prediction import predict_duration, save_prediction


def test_fernet_roundtrip():
    plain = "super-secret-vk-token"
    enc = encrypt_secret(plain)
    assert enc != plain
    assert decrypt_secret(enc) == plain
    assert decrypt_secret("garbage") == ""


def test_mask_email():
    assert "***" in mask_email("user@example.com")
    assert "user@example.com" != mask_email("user@example.com")


def test_payment_prices():
    assert PLAN_PRICES["pro"] == 3500
    assert PLAN_PRICES["enterprise"] == 15000


def test_create_payment_test_mode():
    p = create_payment("user1", "pro")
    assert p["test_mode"] is True
    assert "confirmation_url" in p


def test_verify_webhook_signature_valid_json():
    assert verify_webhook_signature({}, b'{"event": "payment.succeeded"}') is True
    assert verify_webhook_signature({}, b"not-json") is False


def test_webhook_signature_production_unknown_payment(monkeypatch):
    """В проде вебхук с неизвестным платёжным id отклоняется."""
    from app.config import settings

    monkeypatch.setattr(settings, "YOOKASSA_TEST_MODE", False)
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", "123")
    body = b'{"event": "payment.succeeded", "object": {"id": "unknown-pay"}}'
    assert verify_webhook_signature({"x-real-ip": "76.76.244.10"}, body) is False


def test_webhook_signature_production_bad_ip(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "YOOKASSA_TEST_MODE", False)
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", "123")
    body = b'{"event": "payment.succeeded", "object": {"id": "unknown-pay"}}'
    # IP вне доверенных диапазонов -> отказ
    assert verify_webhook_signature({"x-real-ip": "203.0.113.5"}, body) is False
    # нет IP вовсе -> пропускаем IP-проверку (зависит от окружения прокси)
    assert verify_webhook_signature({}, body) is False  # но неизвестный платёж всё равно отклонён


def test_activate_deactivate_subscription_roundtrip():
    """Активация и последующая деактивация подписки через refund."""
    from app.database import SessionLocal
    from app.models.user import User
    from app.services.payments import activate_subscription, deactivate_subscription

    db = SessionLocal()
    try:
        user = User(email="pay@example.com", password_hash="x")
        db.add(user)
        db.commit()
        uid = user.id
    finally:
        db.close()

    activate_subscription(uid, "pro", "pay-abc")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        assert u.plan == "pro"
        assert u.subscription_id
    finally:
        db.close()

    deactivate_subscription(uid)
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == uid).first()
        assert u.plan == "trial"
        assert u.plan_expires_at is None
    finally:
        db.close()


def test_messenger_format():
    text = format_test_result("Login", "passed", 3, 0, "https://example.com")
    assert "Login" in text
    assert "passed" in text


def test_webhook_signature():
    sig = build_signature('{"a":1}', "secret")
    assert len(sig) == 64
    # deterministic
    assert build_signature('{"a":1}', "secret") == sig
    assert build_signature('{"a":1}', "other") != sig


def test_predict_duration():
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        _ensure_user(db, "user-none")
        run = __import__("app.models.test_run", fromlist=["TestRun"]).TestRun(
            owner_id="user-none", url="https://example.com", mode="specific", name="r")
        db.add(run)
        db.commit()
        pred = predict_duration(db, "user-none", 5)
        assert pred >= 10
        assert pred <= 100
        save_prediction(db, run.id, predicted=100, actual=50, num_steps=5)
    finally:
        db.close()


def test_time_prediction_formula_components():
    from app.services.time_prediction import predict_duration
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        p1 = predict_duration(db, "u", 10, 0)
        p2 = predict_duration(db, "u", 10, 10)
        # more elements -> higher prediction
        assert p2 >= p1
    finally:
        db.close()


def test_gigachat_oauth_exchange(monkeypatch):
    """GigaChat должен обменять ключ на OAuth-токен перед запросом к модели."""
    from app.config import settings
    from app.services.ai import AIClient

    monkeypatch.setattr(settings, "GIGACHAT_API_KEY", "test-key-123")
    client = AIClient()
    calls = {}

    def fake_call_http(url, headers, payload):
        calls["url"] = url
        calls["headers"] = headers
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(client, "_gigachat_token", lambda: "test-bearer")
    monkeypatch.setattr(client, "_call_http", fake_call_http)
    text = client._chat_gigachat("sys", "user")
    assert text == "ok"
    assert calls["headers"]["Authorization"] == "Bearer test-bearer"
    assert calls["url"].endswith("/chat/completions")


def test_gigachat_token_requires_auth_endpoint(monkeypatch):
    from app.config import settings
    from app.services.ai import AIClient

    monkeypatch.setattr(settings, "GIGACHAT_API_KEY", "test-key-123")
    client = AIClient()
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "tok-1"}

    def fake_post(url, data=None, headers=None):
        captured["url"] = url
        captured["auth"] = headers.get("Authorization", "")
        captured["rquid"] = bool(headers.get("RqUID"))
        return FakeResp()

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None, headers=None):
            return fake_post(url, data=data, headers=headers)

    monkeypatch.setattr("app.services.ai.httpx.Client", lambda *a, **k: FakeClient())
    token = client._gigachat_token()
    assert token == "tok-1"
    assert captured["url"].endswith("/auth")
    assert captured["auth"].startswith("Basic ")
    assert captured["rquid"] is True


def _ensure_user(db, user_id):
    """PostgreSQL enforces FK: create owner before referencing it."""
    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, email=f"{user_id}@example.com", password_hash="x")
        db.add(user)
        db.commit()
    return user


def test_html_report_escapes_xss():
    """HTML-отчёт не должен пропускать HTML/скрипты из данных теста."""
    import os

    from app.database import SessionLocal
    from app.models.test_run import RunStatus, TestRun, TestStep, StepStatus
    from app.services.reports import generate_html

    db = SessionLocal()
    try:
        _ensure_user(db, "u")
        run = TestRun(owner_id="u", url="https://example.com", name="<script>alert(1)</script>",
                      mode="specific", status=RunStatus.failed.value)
        db.add(run)
        db.commit()
        db.add(TestStep(test_run_id=run.id, order=1, action="click",
                        selector="x", status=StepStatus.failed.value,
                        message="<img src=x onerror=alert(2)>"))
        db.commit()
        html = generate_html(run).decode("utf-8")
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)</script>" not in html
        assert "&lt;img" in html
    finally:
        db.close()


def test_plan_of_trial_expiry():
    """Триал блокируется по истечении срока даже без beat-задачи."""
    from datetime import datetime, timedelta, timezone

    from app.database import SessionLocal
    from app.models.user import Plan, User
    from app.services.limits import plan_of

    db = SessionLocal()
    try:
        old = User(email="old@example.com", password_hash="x", plan=Plan.trial.value,
                   trial_started_at=datetime.now(timezone.utc) - timedelta(days=10))
        db.add(old)
        db.commit()
        assert plan_of(old) == "blocked"

        fresh = User(email="fresh@example.com", password_hash="x", plan=Plan.trial.value,
                     trial_started_at=datetime.now(timezone.utc))
        db.add(fresh)
        db.commit()
        assert plan_of(fresh) == "trial"
    finally:
        db.close()


def test_activate_subscription_idempotent():
    """Повторная активация той же подписки не плодит дубликаты."""
    from app.database import SessionLocal
    from app.models.payment import Subscription
    from app.models.user import User
    from app.services.payments import activate_subscription

    db = SessionLocal()
    try:
        user = User(email="idem@example.com", password_hash="x")
        db.add(user)
        db.commit()
        uid = user.id
    finally:
        db.close()

    activate_subscription(uid, "pro", "pay-idem")
    activate_subscription(uid, "pro", "pay-idem")

    db = SessionLocal()
    try:
        subs = db.query(Subscription).filter(Subscription.user_id == uid).all()
        assert len(subs) == 1
        assert subs[0].payment_id == "pay-idem"
    finally:
        db.close()


def test_sandbox_docker_fallback_local(monkeypatch):
    """Docker-падение даёт ошибку, если локальный fallback выключен (прод), и
    локальный результат — только когда fallback явно разрешён."""
    import sys
    import types

    from app.config import settings
    from app.agent.sandbox import SandboxExecutor

    monkeypatch.setattr(settings, "SANDBOX_RUNNER", "docker")
    monkeypatch.setattr(settings, "SANDBOX_ALLOW_LOCAL_FALLBACK", False)

    def fake_popen(cmd, **kw):
        raise OSError("docker not installed")

    monkeypatch.setattr("app.agent.sandbox.subprocess.Popen", fake_popen)

    ex = SandboxExecutor()
    res = ex.run("https://ya.ru", [], 1)
    assert res["status"] == "error", "без разрешённого fallback Docker-сбой должен давать error"

    # Явное разрешение fallback — снова возвращаем старое поведение.
    monkeypatch.setattr(settings, "SANDBOX_ALLOW_LOCAL_FALLBACK", True)

    def fake_run_tests(**kwargs):
        assert kwargs["url"] == "https://ya.ru"
        return {"status": "passed", "steps": []}

    fake_mod = types.ModuleType("runner")
    fake_mod.run_tests = fake_run_tests
    monkeypatch.setitem(sys.modules, "runner", fake_mod)

    res = ex.run("https://ya.ru", [], 1)
    assert res["status"] == "passed"


def test_sandbox_parse_result_line():
    from app.agent.sandbox import SandboxExecutor

    ex = SandboxExecutor()
    out = "log line\nQA_RESULT:{\"status\": \"failed\", \"steps\": [1]}\nmore"
    res = ex._parse_output(out)
    assert res["status"] == "failed"
    assert ex._parse_output("nothing")["status"] == "error"


def test_sandbox_docker_timeout(monkeypatch):
    import subprocess

    from app.agent.sandbox import SandboxExecutor

    class FakeProc:
        def __init__(self):
            self._called = 0

        def communicate(self, timeout=None):
            self._called += 1
            if self._called == 1:
                raise subprocess.TimeoutExpired("docker", 1)
            return "", ""

        def kill(self):
            pass

    monkeypatch.setattr("app.agent.sandbox.subprocess.Popen", lambda *a, **k: FakeProc())
    ex = SandboxExecutor()
    res = ex._run_docker({"url": "x"}, 0, False)
    assert res["status"] == "timeout"


def test_sandbox_docker_error_parses_stdout(monkeypatch):
    """При returncode != 0 результаты из stdout не теряются."""
    from app.agent.sandbox import SandboxExecutor

    class FakeProc:
        def __init__(self):
            self.returncode = 1

        def communicate(self, timeout=None):
            return 'QA_RESULT:{"status": "failed", "steps": []}', "err"

        def kill(self):
            pass

    monkeypatch.setattr("app.agent.sandbox.subprocess.Popen", lambda *a, **k: FakeProc())
    ex = SandboxExecutor()
    res = ex._run_docker({"url": "x"}, 1, False)
    assert res["status"] == "failed"


def test_ssrf_validation():
    from app.core.ssrf import assert_public_url, validate_url, is_private_host

    assert_public_url("https://example.com")
    assert_public_url("http://ya.ru")

    import pytest as _pytest
    with _pytest.raises(ValueError):
        assert_public_url("ftp://example.com")
    with _pytest.raises(ValueError):
        assert_public_url("http://127.0.0.1/")

    assert validate_url("https://example.com/path") == "https://example.com/path"
    assert is_private_host("http://192.168.1.1/") is True


def test_validator_syntax_and_auth(monkeypatch):
    from app.services.validator import syntax_validate, _check_auth_credentials

    # too short + no action verb -> invalid
    assert syntax_validate("привет").valid is False
    # valid phrase with action + object
    ok = syntax_validate("нажми кнопку Войти, введи в поле логин и пароль, проверь текст Привет")
    assert ok.valid is True
    # login field mentioned but no explicit input command -> auth error
    assert _check_auth_credentials("введи в поле логин test@example.com и пароль в поле пароль") == []
    assert any("логин" in e for e in _check_auth_credentials("нажми кнопку Войти и проверь поле логин"))


def test_validate_scenario_full_path(monkeypatch):
    """Полный двухуровневый валидатор: синтаксис + семантика (AI замокан)."""
    import app.services.validator as v
    from app.services.ai import ai_client

    calls = []

    def fake_chat(system, user):
        calls.append(user)
        return '{"valid": true, "errors": [], "suggestions": []}'

    monkeypatch.setattr(ai_client, "chat", fake_chat)
    good = "нажми кнопку Войти, введи в поле логин test@example.com, проверь что появился текст Привет"
    res = v.validate_scenario(good)
    assert res["valid"] is True
    assert res["semantic"] is True
    assert calls, "семантическая проверка должна была вызвать AI"
    assert v.invalidate_validation_cache() is None


def test_strategist_plan_ordering():
    from app.agent.strategist import Strategist

    st = Strategist()
    plan = st._heuristic_plan([
        {"type": "link", "selector": "a1", "text": "About"},
        {"type": "form", "selector": "form", "text": ""},
        {"type": "button", "selector": "b1", "text": "OK"},
    ])
    assert plan[0]["action"] == "click"
    assert plan[0]["type"] == "link"
    assert plan[0]["element"] == "a1"
    assert plan[1]["type"] == "form"
    assert plan[1]["steps"][0]["action"] == "fill_form"
    assert plan[1]["steps"][-1]["action"] == "check_form"
    assert plan[1]["steps"][-1]["expected_effect"] == "form_submitted"
    assert plan[2]["type"] == "button"
    # form с шагами + кнопки/ссылки имеют хотя бы один step
    assert all(len(p["steps"]) >= 1 for p in plan)


def test_guardian_timeout_fires_on_timeout(monkeypatch):
    """Guardian реально вызывает on_timeout по истечении таймера."""
    from app.agent.guardian import Guardian

    fired = {"hit": False}
    g = Guardian(timeout_seconds=0.05)

    def _hook():
        fired["hit"] = True

    g.start(on_timeout=_hook)
    import time

    time.sleep(0.15)
    assert fired["hit"] is True
    g.cancel()
    # cancel после срабатывания не мешает
    assert g.elapsed() >= 0.05


def test_guardian_cancel_prevents_fire():
    from app.agent.guardian import Guardian

    fired = {"hit": False}
    g = Guardian(timeout_seconds=0.05)

    def _hook():
        fired["hit"] = True

    g.start(on_timeout=_hook)
    g.cancel()
    import time

    time.sleep(0.12)
    assert fired["hit"] is False


def test_keeper_dump_contains_events():
    from app.agent.keeper import Keeper

    k = Keeper("run-1")
    k.log("start", {"url": "https://example.com"})
    k.log("step_result", {"order": 1, "status": "passed"})
    dump = k.dump()
    assert "run-1" in dump
    assert "start" in dump
    assert "step_result" in dump


def test_runner_result_includes_audit(monkeypatch):
    """Итоговый результат runner содержит аудит (keeper.dump) и уважает таймаут."""
    import time

    from app.agent.runner import run_scenario

    class _Nav:
        def run(self, url, steps, timeout, full_page=False, login=None):
            time.sleep(0.01)
            return {"status": "passed", "steps": [{"status": "ok"}]}

    monkeypatch.setattr("app.agent.runner.SandboxExecutor", lambda *a, **k: _Nav())
    res = run_scenario("https://example.com",
                       "нажми кнопку Войти, введи в поле логин test@test.com, нажми кнопку Отправить",
                       test_run_id="x")
    assert res["status"] in ("passed", "failed")
    assert isinstance(res.get("audit"), dict)
    assert "events" in res["audit"]
    assert any(e.get("event") == "start" for e in res["audit"]["events"])


def test_retry_deliveries_uses_explicit_delivery(monkeypatch):
    """Ретрай доставки должен идти по конкретной строке, не плодя дубликатов."""
    from datetime import datetime, timedelta, timezone

    from app.database import SessionLocal
    from app.models.webhook import WebhookConfig, WebhookDelivery
    from app.tasks.notifications import retry_deliveries

    db = SessionLocal()
    try:
        _ensure_user(db, "u")
        cfg = WebhookConfig(owner_id="u", url="https://example.com/hook",
                            event="test_finished", secret="s")
        db.add(cfg)
        db.commit()
        d1 = WebhookDelivery(webhook_id=cfg.id, payload={"a": 1}, success=False,
                             attempts=1, created_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        d2 = WebhookDelivery(webhook_id=cfg.id, payload={"a": 2}, success=False,
                             attempts=1, created_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        db.add_all([d1, d2])
        db.commit()

        sent = []
        orig = __import__("app.services.webhooks", fromlist=["deliver_webhook"]).deliver_webhook

        def fake_deliver(db_, webhook_id, payload, delivery=None):
            sent.append((payload, delivery.id if delivery else None))
            delivery.success = True
            delivery.error = ""
            db_.add(delivery)
            db_.commit()
            return True

        monkeypatch.setattr("app.services.webhooks.deliver_webhook", fake_deliver)
        retry_deliveries()
        # обе строки были ретраены независимо, каждая со своей delivery
        assert len(sent) == 2
        assert {s[1] for s in sent} == {d1.id, d2.id}
    finally:
        db.close()