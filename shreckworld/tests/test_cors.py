from fastapi.testclient import TestClient

from shreckconfig import load_cors_policy
from app.main import app


def test_foundry_preflight_accepts_player_authorization():
    client = TestClient(app)
    response = client.options("/shreckworlds", headers={
        "Origin": "https://foundry.shrecknet.club",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://foundry.shrecknet.club"
    denied = client.options("/shreckworlds", headers={
        "Origin": "https://untrusted.example",
        "Access-Control-Request-Method": "GET",
    })
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_legacy_environment_cannot_override_file_policy(monkeypatch):
    expected = load_cors_policy()
    monkeypatch.setenv("SHRECKWORLD_CORS_ALLOW_ORIGINS", '["https://untrusted.example"]')
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://untrusted.example"]')
    assert load_cors_policy() == expected
