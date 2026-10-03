from fastapi.testclient import TestClient

from stardeck.config import Settings
from stardeck.main import create_app


def test_no_password_means_open(client):
    assert client.get("/api/auth/status").json() == {"required": False, "authenticated": True}
    assert client.get("/api/dashboards").status_code == 200


def test_password_gate(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", password="s3cret", llm_base_url="http://127.0.0.1:9/v1")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/dashboards").status_code == 401
        assert c.get("/api/auth/status").json() == {"required": True, "authenticated": False}
        assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "s3cret"}).status_code == 200
        assert c.get("/api/dashboards").status_code == 200
        c.post("/api/auth/logout")
        assert c.get("/api/dashboards").status_code == 401
