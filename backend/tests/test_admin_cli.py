"""The operator CLI: workspace to signed-in person, and looking after accounts."""

import re

import pytest
from fastapi.testclient import TestClient

from stardeck.admin import main
from stardeck.config import Settings
from stardeck.main import create_app

APP_URL = "https://app.example.com"
PASSWORD = "cli-pass-1234"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", hosted=True,
                    public_url=APP_URL)


def run(settings, capsys, *argv: str) -> str:
    assert main(list(argv), settings=settings) == 0, capsys.readouterr().err
    return capsys.readouterr().out


def token_in(out: str, kind: str) -> str:
    return re.search(rf"{APP_URL}/{kind}/(\S+)", out).group(1)


def test_invitation_to_signed_in_workspace(settings, capsys):
    ws = re.search(r"Created (ws-\w+)", run(settings, capsys, "workspace", "create", "Acme")).group(1)
    out = run(settings, capsys, "invite", "asha@acme.in", "--workspace", ws, "--name", "Asha Rao")
    assert "Hi Asha," in out and "Subject: Your Stardeck invitation" in out
    token = token_in(out, "invite")
    listing = run(settings, capsys, "workspace", "list")
    assert "Acme" in listing and "community" in listing

    with TestClient(create_app(settings), base_url=APP_URL) as c:
        assert c.post(f"/api/auth/invite/{token}", json={"name": "Asha", "password": PASSWORD}).status_code == 200
        assert c.get("/api/auth/status").json()["workspace"]["name"] == "Acme"


def test_plans_suspension_resets_and_sign_outs(settings, capsys):
    out = run(settings, capsys, "workspace", "create", "Globex", "--plan", "enterprise")
    ws = re.search(r"Created (ws-\w+)", out).group(1)
    token = token_in(run(settings, capsys, "invite", "hank@globex.com", "--workspace", ws), "invite")
    app = create_app(settings)
    with TestClient(app, base_url=APP_URL) as c:
        assert c.post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 200
        assert main(["invite", "hank@globex.com", "--workspace", ws], settings=settings) == 1  # already has an account
        capsys.readouterr()

        run(settings, capsys, "workspace", "plan", ws, "community")
        app.state.stardeck.workspaces.invalidate()  # the server would pick it up within 30 s
        assert c.get("/api/auth/status").json()["workspace"]["plan"] == "community"

        run(settings, capsys, "sessions", "revoke", "hank@globex.com")
        assert c.get("/api/dashboards").status_code == 401

        reset = token_in(run(settings, capsys, "user", "reset-link", "hank@globex.com"), "reset")
        assert c.post(f"/api/auth/invite/{reset}", json={"password": "a-new-pass-99"}).status_code == 200
        assert c.get("/api/dashboards").status_code == 200

        run(settings, capsys, "workspace", "suspend", ws)
        app.state.stardeck.workspaces.invalidate()
        assert c.get("/api/dashboards").status_code == 401


def test_deleting_a_workspace_removes_everything(settings, capsys):
    ws = re.search(r"Created (ws-\w+)", run(settings, capsys, "workspace", "create", "Initech")).group(1)
    token = token_in(run(settings, capsys, "invite", "peter@initech.com", "--workspace", ws), "invite")
    app = create_app(settings)
    with TestClient(app, base_url=APP_URL) as c:
        assert c.post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 200
        up = c.post("/api/sources/upload", data={"name": "t"}, files=[("files", ("t.csv", b"a,b\n1,2\n", "text/csv"))])
        assert up.status_code == 201
    folder = settings.data_dir / "uploads" / ws
    assert folder.is_dir()
    assert main(["workspace", "delete", ws], settings=settings) == 1  # needs --yes
    capsys.readouterr()
    run(settings, capsys, "workspace", "delete", ws, "--yes")
    assert not folder.exists()
    assert (settings.data_dir / "demo" / "retail.sqlite").is_file()  # the shared sample data stays
    assert ws not in run(settings, capsys, "workspace", "list")
    with TestClient(create_app(settings), base_url=APP_URL) as c:
        assert c.post("/api/auth/login", json={"username": "peter@initech.com", "password": PASSWORD}).status_code == 401


def test_backup(settings, capsys, tmp_path):
    run(settings, capsys, "workspace", "create", "Umbrella")
    out = run(settings, capsys, "backup", str(tmp_path / "bk"))
    assert "Backed up" in out and list((tmp_path / "bk").glob("stardeck-manual-*.db"))
    assert "applied" in run(settings, capsys, "migrations")


def test_plan_exceptions(settings, capsys):
    ws = re.search(r"Created (ws-\w+)", run(settings, capsys, "workspace", "create", "Hooli")).group(1)
    out = run(settings, capsys, "workspace", "limits", ws, '{"sources": 3, "features": ["measures"]}')
    assert '"sources": 3' in out and '"measures"' in out
    assert main(["workspace", "limits", ws, '{"colour": 1}'], settings=settings) == 1
    assert main(["workspace", "limits", ws, '{"features": ["teleport"]}'], settings=settings) == 1
    capsys.readouterr()
    assert '"sources": 1' in run(settings, capsys, "workspace", "limits", ws, "--clear")
