def _make_scenario(client, auth_headers):
    return client.post("/api/scenarios", json={
        "name": "Login test",
        "url": "https://example.com",
        "mode": "specific",
        "script_text": 'Нажми на кнопку "Войти", введи в поле логин test@test.com',
        "tags": ["smoke", "critical"],
    }, headers=auth_headers)


def test_create_and_list_scenario(client, auth_headers):
    r = _make_scenario(client, auth_headers)
    assert r.status_code == 201
    data = r.json()
    assert data["current_version"] == 1
    assert data["tags"] == ["smoke", "critical"]

    lst = client.get("/api/scenarios", headers=auth_headers)
    assert lst.status_code == 200
    assert len(lst.json()) == 1


def test_update_creates_version(client, auth_headers):
    s = _make_scenario(client, auth_headers).json()
    r = client.put(f"/api/scenarios/{s['id']}", json={"script_text": 'Нажми на кнопку "Войти", введи в поле логин new@mail.ru'},
                   headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["current_version"] == 2

    versions = client.get(f"/api/scenarios/{s['id']}/versions", headers=auth_headers)
    assert versions.status_code == 200
    assert len(versions.json()) == 2


def test_deprecate_requires_comment(client, auth_headers):
    s = _make_scenario(client, auth_headers).json()
    r = client.put(f"/api/scenarios/{s['id']}", json={"status": "deprecated"}, headers=auth_headers)
    assert r.status_code == 400


def test_rollback(client, auth_headers):
    s = _make_scenario(client, auth_headers).json()
    client.put(f"/api/scenarios/{s['id']}", json={"script_text": "изменённый текст"}, headers=auth_headers)
    r = client.post(f"/api/scenarios/{s['id']}/rollback", params={"version": 1}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["current_version"] == 3


def test_delete_scenario(client, auth_headers):
    s = _make_scenario(client, auth_headers).json()
    r = client.delete(f"/api/scenarios/{s['id']}", headers=auth_headers)
    assert r.status_code == 204
    assert client.get(f"/api/scenarios/{s['id']}", headers=auth_headers).status_code == 404


def test_validate_endpoint(client, auth_headers):
    r = client.post("/api/scenarios/validate", json={"script_text": "нажми кнопку и попробуй что-нибудь"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["valid"] is False


def test_scenarios_isolated_per_user(client):
    # other user sees no scenarios
    client.post("/api/auth/register", json={
        "email": "other@example.com", "password": "password123",
        "accepted_tos": True, "accepted_privacy": True})
    from app.models.user import User
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "other@example.com").first()
        u.is_verified = True
        db.commit()
    finally:
        db.close()
    login = client.post("/api/auth/login", json={"email": "other@example.com", "password": "password123"}).json()
    h = {"Authorization": f"Bearer {login['access_token']}"}
    lst = client.get("/api/scenarios", headers=h)
    assert lst.json() == []