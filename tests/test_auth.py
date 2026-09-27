import pytest


def test_register(client):
    r = client.post("/api/auth/register", json={
        "email": "new@example.com", "password": "password123",
        "full_name": "New", "accepted_tos": True, "accepted_privacy": True})
    assert r.status_code == 201
    # duplicate
    r2 = client.post("/api/auth/register", json={
        "email": "new@example.com", "password": "password123",
        "full_name": "New", "accepted_tos": True, "accepted_privacy": True})
    assert r2.status_code == 409


def test_register_requires_consent(client):
    r = client.post("/api/auth/register", json={
        "email": "noconsent@example.com", "password": "password123",
        "full_name": "No", "accepted_tos": False, "accepted_privacy": False})
    assert r.status_code == 400


def test_login_and_me(client, auth_headers):
    me = client.get("/api/auth/me", headers=auth_headers)
    assert me.status_code == 200
    assert me.json()["email"] == "test@example.com"
    assert me.json()["plan"] == "trial"


def test_login_wrong_password(client, test_user):
    r = client.post("/api/auth/login", json={
        "email": test_user["email"], "password": "wrongpass"})
    assert r.status_code == 401


def test_refresh_and_logout(client, test_user):
    login = client.post("/api/auth/login", json={
        "email": test_user["email"], "password": test_user["password"]}).json()
    rt = login["refresh_token"]
    ref = client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert ref.status_code == 200
    new_rt = ref.json()["refresh_token"]
    # old token is rotated -> invalid
    ref2 = client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert ref2.status_code == 401
    # logout revokes new token
    out = client.post("/api/auth/logout", json={"refresh_token": new_rt})
    assert out.status_code == 200
    ref3 = client.post("/api/auth/refresh", json={"refresh_token": new_rt})
    assert ref3.status_code == 401


def test_forgot_password_no_email_leak(client):
    r = client.post("/api/auth/forgot-password", json={"email": "nonexistent@example.com"})
    assert r.status_code == 200
    assert "отправлена" in r.json()["message"]


def test_change_password(client, auth_headers, test_user):
    r = client.post("/api/auth/change-password", json={
        "old_password": "password123", "new_password": "newpassword456"}, headers=auth_headers)
    assert r.status_code == 200
    # old password no longer works
    login = client.post("/api/auth/login", json={
        "email": test_user["email"], "password": "password123"})
    assert login.status_code == 401


def test_verify_email_flow(client):
    # registration creates token; we can't read email, but verify endpoint handles invalid token
    r = client.get("/api/auth/verify-email", params={"token": "invalid-token"})
    assert r.status_code == 400