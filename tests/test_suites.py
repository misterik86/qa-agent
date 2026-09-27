"""Tests for suites, cases, credentials, delta, public reports, CI summary."""
import uuid

import pytest

from app.database import SessionLocal
from app.models.suite import SiteCredential, TestCase, TestSuite
from app.models.test_run import TestRun


def _mk_suite(client, email="suite@example.com", name="Регрессия", url="https://example.com"):
    """Create a user (via API) + suite directly in DB. Returns (suite_id, user_id)."""
    client.post("/api/auth/register", json={
        "email": email, "password": "password123", "full_name": "S",
        "accepted_tos": True, "accepted_privacy": True})
    from app.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        suite = TestSuite(owner_id=user.id, name=name, url=url, mode="specific")
        db.add(suite)
        db.commit()
        db.refresh(suite)
        return str(suite.id), str(user.id)
    finally:
        db.close()


def _login_headers(client, email):
    tok = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ---------- Suites CRUD ----------

def test_suite_crud_and_isolation(client):
    sid, uid = _mk_suite(client)
    headers = _login_headers(client, "suite@example.com")

    resp = client.get("/api/suites", headers=headers)
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert resp.json()[0]["name"] == "Регрессия"

    resp = client.patch(f"/api/suites/{sid}", json={"name": "Переименован"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["name"] == "Переименован"

    # another user cannot see or modify
    client.post("/api/auth/register", json={
        "email": "other@example.com", "password": "password123", "full_name": "O",
        "accepted_tos": True, "accepted_privacy": True})
    other = _login_headers(client, "other@example.com")
    assert client.get(f"/api/suites/{sid}", headers=other).status_code == 404
    assert client.delete(f"/api/suites/{sid}", headers=other).status_code == 404

    # bad url rejected
    assert client.post("/api/suites", json={"name": "x", "url": "ftp://y"},
                       headers=headers).status_code == 400

    assert client.delete(f"/api/suites/{sid}", headers=headers).status_code == 204
    assert client.get(f"/api/suites/{sid}", headers=headers).status_code == 404


def test_cases_crud_and_ordering(client):
    sid, _ = _mk_suite(client)
    headers = _login_headers(client, "suite@example.com")

    c1 = client.post(f"/api/suites/{sid}/cases", json={
        "action": "click", "selector": ".btn", "expected": "Кнопка нажимается",
        "expected_effect": "state_changed"}, headers=headers)
    assert c1.status_code == 201
    c2 = client.post(f"/api/suites/{sid}/cases", json={
        "action": "assert_text", "selector": "h1", "value": "Привет",
        "expected": "Заголовок содержит «Привет»"}, headers=headers)
    assert c2.status_code == 201

    lst = client.get(f"/api/suites/{sid}/cases", headers=headers).json()
    assert [c["order"] for c in lst] == [0, 1]
    cid = lst[0]["id"]

    resp = client.patch(f"/api/suites/cases/{cid}",
                        json={"enabled": False, "expected": "Новое ожидание"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["enabled"] is False

    assert client.delete(f"/api/suites/cases/{cid}", headers=headers).status_code == 204
    assert len(client.get(f"/api/suites/{sid}/cases", headers=headers).json()) == 1


# ---------- Run from suite + delta ----------

@pytest.fixture
def fake_run_steps(monkeypatch):
    """Patch run_steps used by the celery task; returns scripted results per call."""
    calls = []

    def _install(step_outcomes):
        """Queue step statuses applied by the next run_steps invocation."""
        import app.agent.runner as runner_mod

        def fake_run_steps(url, steps, test_run_id="", name="", keeper=None,
                           navigator=None, login=None):
            calls.append({"url": url, "steps": steps, "login": login})
            out = []
            for i, s in enumerate(steps):
                st = step_outcomes[i] if i < len(step_outcomes) else "passed"
                out.append({"status": st, "message": f"msg-{st}", "order": i + 1,
                            "console_error": "", "action": s.get("action"),
                            "selector": s.get("selector"), "value": s.get("value"),
                            "expected": s.get("expected")})
            return {"status": "failed" if any(x.get("status") != "passed" for x in out) else "passed",
                    "steps": out, "console_errors": [], "error": ""}

        monkeypatch.setattr(runner_mod, "run_steps", fake_run_steps)

    _install.calls = calls
    return _install


def _run_task(run):
    from app.tasks.run_test import run_test_task

    run_test_task(run.id, run.owner_id, run.url, run.mode if isinstance(run.mode, str) else run.mode.value,
                  run.script_text, run.name)


def test_run_from_suite_with_login_and_case_ids(client, fake_run_steps):
    sid, uid = _mk_suite(client)
    db = SessionLocal()
    try:
        from app.core.crypto import encrypt_secret

        cred = SiteCredential(owner_id=uid, label="Админка", login_url="https://example.com/login",
                              username="admin", password_encrypted=encrypt_secret("s3cret"))
        db.add(cred)
        db.commit()
        suite = db.query(TestSuite).filter(TestSuite.id == sid).first()
        suite.credential_id = cred.id
        case_a = TestCase(suite_id=sid, order=0, action="click", selector=".a", expected="A")
        case_b = TestCase(suite_id=sid, order=1, action="assert_text", selector="h1", expected="B")
        db.add_all([case_a, case_b])
        disabled = TestCase(suite_id=sid, order=2, action="click", selector=".off", expected="X", enabled=False)
        db.add(disabled)
        run = TestRun(owner_id=uid, name="Набор", url=suite.url, mode="specific",
                      status="queued", suite_id=sid)
        db.add(run)
        db.commit()

        fake_run_steps(["passed", "failed"])
        _run_task(run)
        db.expire_all()

        assert fake_run_steps.calls[0]["login"]["username"] == "admin"
        assert fake_run_steps.calls[0]["login"]["password"] == "s3cret"
        assert len(fake_run_steps.calls[0]["steps"]) == 2  # disabled case excluded

        run = db.query(TestRun).filter(TestRun.id == run.id).first()
        assert run.status == "failed" and run.failed_steps == 1
        step_cids = [s.test_case_id for s in sorted(run.steps, key=lambda x: x.order)]
        assert step_cids == [case_a.id, case_b.id]
    finally:
        db.close()


def test_delta_regressed_then_fixed(client, fake_run_steps):
    sid, uid = _mk_suite(client)
    db = SessionLocal()
    try:
        suite = db.query(TestSuite).filter(TestSuite.id == sid).first()
        case = TestCase(suite_id=sid, order=0, action="click", selector=".x", expected="Кейс X")
        db.add(case)
        r1 = TestRun(owner_id=uid, name="R1", url=suite.url, mode="specific", status="queued", suite_id=sid)
        db.add(r1)
        db.commit()

        fake_run_steps(["passed"])
        _run_task(r1)

        r2 = TestRun(owner_id=uid, name="R2", url=suite.url, mode="specific", status="queued", suite_id=sid)
        db.add(r2)
        db.commit()
        fake_run_steps(["failed"])
        _run_task(r2)

        r3 = TestRun(owner_id=uid, name="R3", url=suite.url, mode="specific", status="queued", suite_id=sid)
        db.add(r3)
        db.commit()
        fake_run_steps(["passed"])
        _run_task(r3)

        headers = _login_headers(client, "suite@example.com")
        d2 = client.get(f"/api/runs/{r2.id}", headers=headers).json()["delta"]
        assert d2["regressed"] == ["Кейс X"]
        d3 = client.get(f"/api/runs/{r3.id}", headers=headers).json()["delta"]
        assert d3["fixed"] == ["Кейс X"]
        # first run has no delta (nothing to compare)
        assert client.get(f"/api/runs/{r1.id}", headers=headers).json()["delta"] is None
    finally:
        db.close()


# ---------- Save as suite ----------

def test_save_as_suite_from_run(client):
    client.post("/api/auth/register", json={
        "email": "scan@example.com", "password": "password123", "full_name": "S",
        "accepted_tos": True, "accepted_privacy": True})
    from app.models.user import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "scan@example.com").first()
        run = TestRun(owner_id=u.id, name="Скан", url="https://example.com", mode="full_page",
                      status="passed", total_steps=2,
                      result_json={"steps": [
                          {"action": "click", "selector": ".b1", "expected": "E1", "expected_effect": "state_changed"},
                          {"action": "validate_field", "selector": "form input[name=email]", "expected": "E2"},
                          {"action": "click", "selector": ".b1", "expected": "E1"},  # duplicate -> skipped
                      ]})
        db.add(run)
        db.commit()
        rid = run.id
        db.expire_all()
    finally:
        db.close()

    headers = _login_headers(client, "scan@example.com")
    resp = client.post(f"/api/runs/{rid}/save-as-suite", json={"name": "Мой набор"}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["cases_count"] == 2  # dedup worked

    suites = client.get("/api/suites", headers=headers).json()
    assert suites[0]["name"] == "Мой набор"
    cases = client.get(f"/api/suites/{suites[0]['id']}/cases", headers=headers).json()
    assert all(c["source"] == "auto" for c in cases)


# ---------- Credentials ----------

def test_credentials_encrypted_not_leaked_and_delete_guard(client):
    client.post("/api/auth/register", json={
        "email": "cred@example.com", "password": "password123", "full_name": "C",
        "accepted_tos": True, "accepted_privacy": True})
    headers = _login_headers(client, "cred@example.com")

    resp = client.post("/api/credentials", json={
        "label": "Прод", "login_url": "https://example.com/login",
        "username": "qa@ex.com", "password": "topsecret"}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert "password" not in body and "password_encrypted" not in body

    from app.core.crypto import decrypt_secret

    db = SessionLocal()
    try:
        from app.models.suite import SiteCredential as SC

        cred = db.query(SC).first()
        assert "topsecret" not in (cred.password_encrypted or "")
        assert decrypt_secret(cred.password_encrypted) == "topsecret"
        cred_id = cred.id
    finally:
        db.close()

    # patch without password keeps the old one
    client.patch(f"/api/credentials/{cred_id}", json={"label": "Новое имя"}, headers=headers)
    from app.core.crypto import decrypt_secret as dec

    db = SessionLocal()
    try:
        from app.models.suite import SiteCredential as SC

        cred = db.query(SC).filter(SC.id == cred_id).first()
        assert dec(cred.password_encrypted) == "topsecret"

        # guard: cannot delete while a suite uses it
        from app.models.user import User

        user = db.query(User).filter(User.email == "cred@example.com").first()
        suite = TestSuite(owner_id=user.id, name="S", url="https://example.com",
                          mode="specific", credential_id=cred_id)
        db.add(suite)
        db.commit()
    finally:
        db.close()
    resp = client.delete(f"/api/credentials/{cred_id}", headers=headers)
    assert resp.status_code == 409
    # unbind then delete works ("" clears the binding)
    sid = client.get("/api/suites", headers=headers).json()[0]["id"]
    resp_unbind = client.patch(f"/api/suites/{sid}", json={"credential_id": ""}, headers=headers)
    assert resp_unbind.status_code == 200 and resp_unbind.json()["credential_id"] is None
    assert client.delete(f"/api/credentials/{cred_id}", headers=headers).status_code == 204


# ---------- Public report / share ----------

def test_share_public_report_and_revoke(client):
    sid, uid = _mk_suite(client, email="share@example.com")
    headers = _login_headers(client, "share@example.com")

    db = SessionLocal()
    try:
        run = TestRun(owner_id=uid, name="Публичный", url="https://example.com",
                      mode="specific", status="passed", total_steps=1, passed_steps=1,
                      result_json={"steps": [{"order": 1}]},
                      suite_id=sid)
        db.add(run)
        db.commit()
        rid = run.id
    finally:
        db.close()

    resp = client.post(f"/api/runs/{rid}/share", headers=headers)
    assert resp.status_code == 200
    token = resp.json()["token"]
    # stable token on repeat
    assert client.post(f"/api/runs/{rid}/share", headers=headers).json()["token"] == token

    # anonymous access works
    pub = client.get(f"/api/public/reports/{token}")
    assert pub.status_code == 200
    assert pub.json()["name"] == "Публичный"
    # page route serves html
    assert client.get(f"/report/{token}").status_code == 200

    # revoke -> 404
    assert client.delete(f"/api/runs/{rid}/share", headers=headers).status_code == 204
    assert client.get(f"/api/public/reports/{token}").status_code == 404


# ---------- Bug report ----------

def test_bug_report_markdown_contains_failed_cases(client):
    client.post("/api/auth/register", json={
        "email": "bug@example.com", "password": "password123", "full_name": "B",
        "accepted_tos": True, "accepted_privacy": True})
    from datetime import datetime, timezone as tz

    from app.models.test_run import TestStep
    from app.models.user import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "bug@example.com").first()
        run = TestRun(owner_id=u.id, name="Бажный", url="https://example.com",
                      mode="specific", status="failed", total_steps=2, passed_steps=1, failed_steps=1)
        db.add(run)
        db.flush()
        db.add(TestStep(test_run_id=run.id, order=1, action="click", selector=".ok",
                        expected="Работает", status="passed", message="ок"))
        db.add(TestStep(test_run_id=run.id, order=2, action="check_form", selector="form",
                        expected="Форма отправляется", status="failed",
                        message="Подтверждение не появилось", console_error="TypeError: x is undefined"))
        db.commit()
        rid = run.id
    finally:
        db.close()

    headers = _login_headers(client, "bug@example.com")
    resp = client.get(f"/api/runs/{rid}/bugreport", headers=headers)
    assert resp.status_code == 200
    text = resp.text
    assert "Баг-репорт: Бажный" in text
    assert "Форма отправляется" in text
    assert "TypeError" in text
    assert "Работает" not in text  # passed cases are not included


# ---------- Developer API: suite run + CI summary ----------

def test_developer_api_suite_and_summary(client, fake_run_steps):
    sid, uid = _mk_suite(client, email="devapi@example.com")
    db = SessionLocal()
    try:
        db.add(TestCase(suite_id=sid, order=0, action="click", selector=".ci", expected="CI-кейс"))
        db.commit()
    finally:
        db.close()

    headers = _login_headers(client, "devapi@example.com")
    key_resp = client.post("/api/keys", json={"name": "ci"}, headers=headers)
    api_key = key_resp.json()["key"]
    ci_headers = {"X-API-Key": api_key}

    # Install the fake BEFORE creating the run: with CELERY_ALWAYS_EAGER the task
    # executes synchronously inside POST /api/v1/tests/run.
    fake_run_steps(["passed"])
    resp = client.post("/api/v1/tests/run", json={
        "suite_id": sid, "url": "https://example.com", "mode": "specific", "consent": True},
        headers=ci_headers)
    assert resp.status_code == 202
    run_id = resp.json()["id"]
    assert resp.json()["suite_id"] == sid

    summary = client.get(f"/api/v1/tests/runs/{run_id}/summary", headers=ci_headers)
    assert summary.status_code == 200
    s = summary.json()
    assert s["finished"] is True and s["status"] == "passed" and s["total_steps"] == 1
