def _auth(client, email="t@example.com"):
    client.post("/api/auth/register", json={
        "email": email, "password": "password123", "accepted_tos": True, "accepted_privacy": True})
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_verified = True
        db.commit()
    finally:
        db.close()
    login = client.post("/api/auth/login", json={"email": email, "password": "password123"}).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


def test_run_requires_consent(client):
    h = _auth(client)
    r = client.post("/api/runs", json={"url": "https://example.com", "consent": False}, headers=h)
    assert r.status_code == 400
    assert "право тестировать" in r.json()["detail"]


def test_run_rejects_private_url(client):
    h = _auth(client)
    r = client.post("/api/runs", json={
        "url": "http://127.0.0.1/", "consent": True, "mode": "specific",
        "script_text": "нажми кнопку"}, headers=h)
    assert r.status_code == 400
    assert "запрещён" in r.json()["detail"]


def test_run_rejects_bad_scheme(client):
    h = _auth(client)
    r = client.post("/api/runs", json={"url": "ftp://example.com", "consent": True}, headers=h)
    assert r.status_code == 400


def test_run_without_scenario(client):
    h = _auth(client)
    r = client.post("/api/runs", json={
        "url": "https://example.com", "consent": True, "mode": "specific",
        "script_text": "нажми на кнопку Войти, введи в поле логин test@test.com"}, headers=h)
    assert r.status_code == 201
    run_id = r.json()["id"]
    # cancel should work
    c = client.post(f"/api/runs/{run_id}/cancel", headers=h)
    assert c.status_code == 200
    # unknown run
    nf = client.get("/api/runs/nope", headers=h)
    assert nf.status_code == 404
    # bad report format
    bad = client.get(f"/api/runs/{run_id}/report/bad", headers=h)
    assert bad.status_code == 400


def test_runs_isolated_per_user(client):
    h1 = _auth(client, "u1@example.com")
    r = client.post("/api/runs", json={
        "url": "https://example.com", "consent": True, "mode": "specific",
        "script_text": "нажми на кнопку Войти, введи в поле логин test@test.com"}, headers=h1)
    assert r.status_code == 201
    h2 = _auth(client, "u2@example.com")
    lst = client.get("/api/runs", headers=h2).json()
    assert lst == []


def test_schedule_crud(client):
    h = _auth(client)
    sc = client.post("/api/scenarios", json={
        "name": "S", "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку, введи в поле логин test@test.com"}, headers=h).json()
    s = client.post("/api/schedules", json={
        "scenario_id": sc["id"], "frequency": "daily", "channel": "email",
        "email_recipient": "x@example.com"}, headers=h)
    assert s.status_code == 201
    sid = s.json()["id"]
    assert len(client.get("/api/schedules", headers=h).json()) == 1
    p = client.post(f"/api/schedules/{sid}/pause", headers=h)
    assert p.status_code == 200
    r = client.post(f"/api/schedules/{sid}/resume", headers=h)
    assert r.status_code == 200
    assert client.delete(f"/api/schedules/{sid}", headers=h).status_code == 204


def test_schedule_requires_owned_scenario(client):
    h = _auth(client)
    r = client.post("/api/schedules", json={
        "scenario_id": "not-a-scenario", "frequency": "daily", "channel": "email"})
    # unauthenticated -> 401/403
    assert r.status_code in (401, 403)


def test_webhook_crud(client):
    h = _auth(client)
    w = client.post("/api/webhooks", json={"url": "https://example.com/hook", "secret": "s"}, headers=h)
    assert w.status_code == 201
    wid = w.json()["id"]
    assert len(client.get("/api/webhooks", headers=h).json()) == 1
    bad = client.post("/api/webhooks", json={"url": "ftp://x"}, headers=h)
    assert bad.status_code == 400
    assert client.delete(f"/api/webhooks/{wid}", headers=h).status_code == 204


def test_schedule_rejects_invalid_enums(client):
    """Недопустимые frequency/channel должны отклоняться (422), а не падать в БД."""
    h = _auth(client)
    sc = client.post("/api/scenarios", json={
        "name": "S", "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку, введи в поле логин test@test.com"}, headers=h).json()
    bad_freq = client.post("/api/schedules", json={
        "scenario_id": sc["id"], "frequency": "monthly", "channel": "email"}, headers=h)
    assert bad_freq.status_code == 422
    bad_channel = client.post("/api/schedules", json={
        "scenario_id": sc["id"], "frequency": "daily", "channel": "sms"}, headers=h)
    assert bad_channel.status_code == 422


def test_webhook_rejects_invalid_event(client):
    h = _auth(client)
    bad = client.post("/api/webhooks", json={"url": "https://example.com/hook", "event": "hack"}, headers=h)
    assert bad.status_code == 422


def test_mock_confirm_rejects_invalid_plan(client):
    h = _auth(client)
    r = client.post("/api/payments/mock-confirm", params={"plan": "gold"}, headers=h)
    assert r.status_code == 400


def test_api_keys_flow(client):
    h = _auth(client)
    k = client.post("/api/keys", json={"name": "ci"}, headers=h)
    assert k.status_code == 201
    full_key = k.json()["key"]
    assert full_key.startswith("qa_")
    assert len(client.get("/api/keys", headers=h).json()) == 1
    assert client.delete(f"/api/keys/{k.json()['id']}", headers=h).status_code == 204


def test_payment_create_and_status(client):
    h = _auth(client)
    p = client.post("/api/payments/create", json={"plan": "pro"}, headers=h)
    assert p.status_code == 200
    assert p.json()["test_mode"] is True
    bad = client.post("/api/payments/create", json={"plan": "bogus"}, headers=h)
    assert bad.status_code == 422
    st = client.get("/api/payments/status", headers=h)
    assert st.status_code == 200


def test_payment_mock_confirm(client):
    h = _auth(client)
    me = client.get("/api/auth/me", headers=h).json()
    r = client.post("/api/payments/mock-confirm", params={"plan": "pro"}, headers=h)
    assert r.status_code == 200
    st = client.get("/api/payments/status", headers=h).json()
    assert st["active"] is True
    assert st["plan"] == "pro"
    # must be authenticated
    anon = client.post("/api/payments/mock-confirm", params={"plan": "pro"})
    assert anon.status_code == 401


def test_payment_webhook(client):
    h = _auth(client)
    me = client.get("/api/auth/me", headers=h).json()
    payload = {
        "event": "payment.succeeded",
        "object": {"id": "pay1", "status": "succeeded", "metadata": {"user_id": me["id"], "plan": "enterprise"}},
    }
    r = client.post("/api/payments/webhook", json=payload)
    assert r.status_code == 200
    st = client.get("/api/payments/status", headers=h).json()
    assert st["plan"] == "enterprise"
    # bad signature
    r2 = client.post("/api/payments/webhook", content=b"not json", headers={"Content-Type": "application/json"})
    assert r2.status_code == 400


def test_payment_refund_deactivates(client):
    h = _auth(client)
    me = client.get("/api/auth/me", headers=h).json()
    r = client.post("/api/payments/mock-confirm", params={"plan": "pro"}, headers=h)
    assert r.status_code == 200
    st = client.get("/api/payments/status", headers=h).json()
    assert st["active"] is True
    payload = {
        "event": "refund.succeeded",
        "object": {"id": "pay2", "status": "succeeded", "metadata": {"user_id": me["id"], "plan": "pro"}},
    }
    r2 = client.post("/api/payments/webhook", json=payload)
    assert r2.status_code == 200
    st2 = client.get("/api/payments/status", headers=h).json()
    assert st2["active"] is False


def test_dashboard_stats(client):
    h = _auth(client)
    s = client.get("/api/dashboard/stats", headers=h)
    assert s.status_code == 200
    assert "total_runs" in s.json()


def test_health_and_metrics(client):
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_developer_api_run(client):
    h = _auth(client)
    k = client.post("/api/keys", json={"name": "ci"}, headers=h).json()
    r = client.post("/api/v1/tests/run", json={
        "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку Войти, введи в поле логин test@test.com",
        "name": "CI run", "consent": True},
        headers={"X-API-Key": k["key"]})
    assert r.status_code == 202
    # consent is mandatory
    r0 = client.post("/api/v1/tests/run", json={
        "url": "https://example.com", "mode": "specific",
        "script_text": "нажми кнопку Войти", "name": "no consent"},
        headers={"X-API-Key": k["key"]})
    assert r0.status_code == 400
    # bad key
    r2 = client.post("/api/v1/tests/run", json={
        "url": "https://example.com", "mode": "specific", "script_text": "x", "consent": True},
        headers={"X-API-Key": "qa_bad"})
    assert r2.status_code == 401


def test_usage_endpoint(client):
    h = _auth(client)
    r = client.get("/api/limits/usage", headers=h)
    assert r.status_code == 200
    # must be authenticated
    anon = client.get("/api/limits/usage")
    assert anon.status_code == 401


def test_trial_expiry_block(client):
    from datetime import datetime, timedelta, timezone
    from app.database import SessionLocal
    from app.models.user import Plan, User

    h = _auth(client, "exp@example.com")
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "exp@example.com").first()
        u.plan = Plan.blocked.value
        u.trial_started_at = datetime.now(timezone.utc) - timedelta(days=10)
        db.commit()
    finally:
        db.close()
    r = client.post("/api/runs", json={
        "url": "https://example.com", "consent": True, "mode": "specific",
        "script_text": "нажми кнопку, введи в поле логин test@test.com"}, headers=h)
    assert r.status_code == 403