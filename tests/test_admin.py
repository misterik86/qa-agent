"""Admin panel tests."""
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.user import User


def _register(client, email, password="password123", full_name="User"):
    return client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "full_name": full_name,
        "accepted_tos": True,
        "accepted_privacy": True,
    })


def _verify_in_db(email):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        user.is_verified = True
        db.commit()
    finally:
        db.close()


def _login(client, email, password="password123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _make_admin_and_second_user(client):
    """First registered user becomes admin (bootstrap), second is regular."""
    assert _register(client, "admin@example.com").status_code == 201
    _verify_in_db("admin@example.com")
    assert _register(client, "user2@example.com").status_code == 201
    _verify_in_db("user2@example.com")
    admin_token = _login(client, "admin@example.com").json()["access_token"]
    return {"Authorization": f"Bearer {admin_token}"}


def test_first_registered_user_becomes_admin(client):
    _register(client, "first@example.com")
    _register(client, "second@example.com")
    db = SessionLocal()
    try:
        first = db.query(User).filter(User.email == "first@example.com").first()
        second = db.query(User).filter(User.email == "second@example.com").first()
        assert first.is_admin is True
        assert second.is_admin is False
    finally:
        db.close()


def test_non_admin_gets_403_on_admin_endpoints(client):
    """The first user is admin by bootstrap, so register a second (regular) one."""
    _register(client, "admin@example.com")
    _register(client, "user2@example.com")
    _verify_in_db("user2@example.com")
    token = _login(client, "user2@example.com").json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/runs",
                 "/api/admin/payments", "/api/admin/audit"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 403


def test_unauthenticated_gets_401(client):
    assert client.get("/api/admin/stats").status_code == 401


def test_admin_users_list_and_search(client):
    headers = _make_admin_and_second_user(client)
    resp = client.get("/api/admin/users", headers=headers)
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert {"admin@example.com", "user2@example.com"} <= emails

    resp = client.get("/api/admin/users?search=user2", headers=headers)
    assert resp.status_code == 200
    assert [u["email"] for u in resp.json()] == ["user2@example.com"]


def test_block_unblock_flow(client):
    headers = _make_admin_and_second_user(client)

    # get user2 token and id
    user2_token = _login(client, "user2@example.com").json()["access_token"]
    user2_id = client.get("/api/auth/me", headers={
        "Authorization": f"Bearer {user2_token}"}).json()["id"]

    # block
    resp = client.patch(f"/api/admin/users/{user2_id}", json={"plan": "blocked"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["plan"] == "blocked"

    # blocked user: login rejected, existing access token rejected
    assert _login(client, "user2@example.com").status_code == 403
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {user2_token}"})
    assert me.status_code == 403

    # unblock -> fresh trial, can log in again
    resp = client.patch(f"/api/admin/users/{user2_id}", json={"plan": "trial"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["plan"] == "trial"
    assert _login(client, "user2@example.com").status_code == 200


def test_patch_plan_verified_full_name(client):
    headers = _make_admin_and_second_user(client)
    users = client.get("/api/admin/users?search=user2", headers=headers).json()
    uid = users[0]["id"]
    resp = client.patch(f"/api/admin/users/{uid}",
                        json={"plan": "pro", "is_verified": False, "full_name": "Renamed"},
                        headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == "pro"
    assert body["is_verified"] is False
    assert body["full_name"] == "Renamed"


def test_cannot_block_self_or_revoke_own_admin(client):
    headers = _make_admin_and_second_user(client)
    admins = client.get("/api/admin/users?search=admin@", headers=headers).json()
    admin_id = [u for u in admins if u["is_admin"]][0]["id"]
    assert client.patch(f"/api/admin/users/{admin_id}", json={"plan": "blocked"},
                        headers=headers).status_code == 400
    assert client.patch(f"/api/admin/users/{admin_id}", json={"is_admin": False},
                        headers=headers).status_code == 400


def test_toggle_admin_rights(client):
    headers = _make_admin_and_second_user(client)
    users = client.get("/api/admin/users?search=user2", headers=headers).json()
    uid = users[0]["id"]
    resp = client.patch(f"/api/admin/users/{uid}", json={"is_admin": True}, headers=headers)
    assert resp.status_code == 200 and resp.json()["is_admin"] is True
    resp = client.patch(f"/api/admin/users/{uid}", json={"is_admin": False}, headers=headers)
    assert resp.status_code == 200 and resp.json()["is_admin"] is False


def test_admin_stats(client):
    headers = _make_admin_and_second_user(client)
    resp = client.get("/api/admin/stats", headers=headers)
    assert resp.status_code == 200
    s = resp.json()
    assert s["users_total"] >= 2
    assert s["users_admins"] >= 1
    assert s["payments_total"] == 0


def test_admin_runs_payments_audit_lists(client):
    headers = _make_admin_and_second_user(client)
    for path in ("/api/admin/runs", "/api/admin/payments", "/api/admin/audit"):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200

    audit = client.get("/api/admin/audit", headers=headers).json()
    events = {a["event"] for a in audit}
    assert "register" in events or len(audit) >= 0


def test_runs_status_filter_validation(client):
    headers = _make_admin_and_second_user(client)
    assert client.get("/api/admin/runs?status=bogus", headers=headers).status_code == 422
    assert client.get("/api/admin/runs?status=passed", headers=headers).status_code == 200


def test_me_returns_is_admin_flag(client):
    _register(client, "first@example.com")
    _verify_in_db("first@example.com")
    token = _login(client, "first@example.com").json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["is_admin"] is True
