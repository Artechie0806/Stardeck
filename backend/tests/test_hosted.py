"""Hosted mode: accounts by invitation, sign-in safety, and no access to the server's files."""

import time

import pytest
from fastapi.testclient import TestClient

from stardeck.config import Settings
from stardeck.db import InviteRecord, SourceRecord
from stardeck.invites import Invites, token_hash
from stardeck.main import create_app

APP_URL = "https://app.example.com"
PASSWORD = "hosted-pass-123"


def hosted_settings(tmp_path, **kw) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", hosted=True,
                    public_url=APP_URL, operator_emails=["ops@stardeck.app"], **kw)


@pytest.fixture
def hosted(tmp_path):
    app = create_app(hosted_settings(tmp_path))
    with TestClient(app, base_url=APP_URL) as c:  # https, so Secure cookies are sent back
        yield app, c


def browser(app) -> TestClient:
    return TestClient(app, base_url=APP_URL)


def join(app, email: str = "asha+test@acme.in", plan: str = "community", role: str = "admin") -> tuple[TestClient, str]:
    """A workspace with one person, joined through an invitation link."""
    state = app.state.stardeck
    ws = state.create_workspace("Acme", plan)
    token = Invites(state.db).create(ws.id, email, role)
    c = browser(app)
    res = c.post(f"/api/auth/invite/{token}", json={"name": "Asha", "password": PASSWORD})
    assert res.status_code == 200, res.text
    return c, ws.id


def test_hosted_mode_refuses_to_start_half_configured(tmp_path):
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        create_app(Settings(_env_file=None, data_dir=tmp_path / "d", hosted=True))
    with pytest.raises(RuntimeError, match="PASSWORD"):
        create_app(Settings(_env_file=None, data_dir=tmp_path / "d", hosted=True, public_url=APP_URL, password="x"))


def test_invitation_creates_the_account_and_signs_in(hosted):
    app, _ = hosted
    state = app.state.stardeck
    ws = state.create_workspace("Acme", "community")
    token = Invites(state.db).create(ws.id, "asha@acme.in")
    c = browser(app)
    assert c.get(f"/api/auth/invite/{token}").json() == {"kind": "invite", "email": "asha@acme.in", "workspace": "Acme"}
    res = c.post(f"/api/auth/invite/{token}", json={"name": "Asha", "password": PASSWORD})
    assert res.status_code == 200
    assert "Secure" in res.headers["set-cookie"] and "HttpOnly" in res.headers["set-cookie"]
    status = c.get("/api/auth/status").json()
    assert status["hosted"] and status["authenticated"] and status["workspace"]["name"] == "Acme"
    assert status["user"]["role"] == "admin"
    # The sample data is there, in their workspace.
    assert [s["name"] for s in c.get("/api/sources").json()] == ["Retail demo"]
    # A link works once.
    assert browser(app).get(f"/api/auth/invite/{token}").status_code == 404
    assert browser(app).post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 404


def test_expired_links_and_setup_are_refused(hosted):
    app, c = hosted
    state = app.state.stardeck
    ws = state.create_workspace("Acme", "community")
    token = Invites(state.db).create(ws.id, "late@acme.in")
    with state.db.platform_session() as s:
        s.get(InviteRecord, token_hash(token)).expires_at = time.time() - 1
        s.commit()
    assert c.get(f"/api/auth/invite/{token}").status_code == 404
    assert c.post("/api/auth/setup", json={"username": "x@y.in", "password": PASSWORD}).status_code == 404
    assert c.get("/api/dashboards").status_code == 401


def test_sign_in_is_by_email(hosted):
    app, _ = hosted
    c, _ = join(app, "Asha+Test@Acme.in", plan="enterprise")  # adding people is an Enterprise feature
    fresh = browser(app)
    assert fresh.post("/api/auth/login", json={"username": "ASHA+test@acme.in", "password": PASSWORD}).status_code == 200
    # Admins add people by email.
    bad = c.post("/api/users", json={"username": "ravi", "password": PASSWORD, "role": "viewer"})
    assert bad.status_code == 400 and "email" in bad.json()["detail"]
    assert c.post("/api/users", json={"username": "ravi@acme.in", "password": PASSWORD}).status_code == 201
    # One email, one account, across all workspaces.
    other, _ = join(app, "zoe@other.in", plan="enterprise")
    dup = other.post("/api/users", json={"username": "ravi@acme.in", "password": PASSWORD})
    assert dup.status_code == 400 and "already" in dup.json()["detail"]


def test_password_reset_signs_out_everywhere(hosted):
    app, _ = hosted
    state = app.state.stardeck
    c, _ = join(app)
    user = state.users.find("asha+test@acme.in")
    token = Invites(state.db).create(user.workspace_id, user.username, user.role, "reset", user_id=user.id)
    phone = browser(app)
    assert phone.get(f"/api/auth/invite/{token}").json()["kind"] == "reset"
    assert phone.post(f"/api/auth/invite/{token}", json={"password": "brand-new-pass"}).status_code == 200
    assert c.get("/api/dashboards").status_code == 401  # the old session is gone
    assert phone.get("/api/dashboards").status_code == 200
    assert browser(app).post("/api/auth/login", json={"username": user.username, "password": PASSWORD}).status_code == 401
    assert browser(app).post("/api/auth/login", json={"username": user.username, "password": "brand-new-pass"}).status_code == 200


def test_changing_your_password(hosted):
    app, _ = hosted
    c, _ = join(app)
    other = browser(app)
    assert other.post("/api/auth/login", json={"username": "asha+test@acme.in", "password": PASSWORD}).status_code == 200
    assert c.post("/api/auth/password", json={"current": "wrong-one", "new": "next-pass-123"}).status_code == 400
    assert c.post("/api/auth/password", json={"current": PASSWORD, "new": "short"}).status_code == 400
    assert c.post("/api/auth/password", json={"current": PASSWORD, "new": "next-pass-123"}).status_code == 200
    assert c.get("/api/dashboards").status_code == 200  # still signed in here
    assert other.get("/api/dashboards").status_code == 401  # signed out elsewhere


def test_sessions_survive_a_restart(tmp_path):
    settings = hosted_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url=APP_URL):
        c, _ = join(app)
        cookie = c.cookies.get("stardeck_session")
    app2 = create_app(settings)
    with TestClient(app2, base_url=APP_URL) as c2:
        c2.cookies.set("stardeck_session", cookie)
        assert c2.get("/api/dashboards").status_code == 200


def test_suspended_workspaces_cant_sign_in(hosted):
    app, _ = hosted
    state = app.state.stardeck
    c, ws = join(app)
    state.workspaces.update(ws, status="suspended")
    assert c.get("/api/dashboards").status_code == 401
    assert browser(app).post("/api/auth/login", json={"username": "asha+test@acme.in", "password": PASSWORD}).status_code == 401
    state.workspaces.update(ws, status="active")
    assert c.get("/api/dashboards").status_code == 200


def test_repeated_wrong_passwords_are_slowed_down(hosted):
    app, _ = hosted
    join(app)
    c = browser(app)
    codes = [c.post("/api/auth/login", json={"username": "asha+test@acme.in", "password": "nope-nope"}).status_code
             for _ in range(9)]
    assert codes[:8] == [401] * 8 and codes[8] == 429
    blocked = c.post("/api/auth/login", json={"username": "asha+test@acme.in", "password": PASSWORD})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


def test_cross_site_requests_are_refused(hosted):
    app, _ = hosted
    c, _ = join(app)
    evil = c.post("/api/dashboards", json={"name": "x", "sourceIds": []}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = c.post("/api/dashboards", json={"name": "x", "sourceIds": []}, headers={"Origin": APP_URL})
    assert same.status_code == 400  # reached the endpoint (no source picked)


def test_no_server_files_as_data_sources(hosted, tmp_path):
    app, _ = hosted
    c, ws = join(app)
    secret = tmp_path / "data" / "stardeck.db"
    for path in ("/api/sources/test", "/api/sources"):
        res = c.post(path, json={"name": "x", "config": {"kind": "sqlite", "path": str(secret)}})
        assert res.status_code == 400 and "Upload" in res.json()["detail"]
    # The sample source can only be renamed: its file can't be pointed elsewhere.
    sample = c.get("/api/sources").json()[0]
    assert "path" not in sample["config"]
    res = c.put(f"/api/sources/{sample['id']}", json={"name": "Renamed", "config": {"kind": "sqlite", "path": str(secret)}})
    assert res.status_code == 200 and res.json()["name"] == "Renamed"
    with app.state.stardeck.db.new_session(ws) as db:
        assert db.get(SourceRecord, sample["id"]).config["path"].endswith("retail.sqlite")


def test_uploads_cant_be_aimed_at_other_files(client, settings, tmp_path):
    """Self-hosted too: changing an upload's path is ignored, and deleting it never removes other files."""
    up = client.post("/api/sources/upload", data={"name": "t"}, files=[("files", ("t.csv", b"a,b\n1,2\n", "text/csv"))])
    assert up.status_code == 201
    victim = tmp_path / "precious.txt"
    victim.write_text("keep me")
    res = client.put(f"/api/sources/{up.json()['id']}", json={"name": "t2", "config": {"kind": "csv", "path": str(victim)}})
    assert res.status_code == 200 and res.json()["name"] == "t2"
    assert client.delete(f"/api/sources/{up.json()['id']}").status_code == 204
    assert victim.read_text() == "keep me"


def test_oversized_uploads_are_refused_early(tmp_path):
    app = create_app(hosted_settings(tmp_path, max_upload_mb=1))
    with TestClient(app, base_url=APP_URL):
        c, _ = join(app)
        big = b"a,b\n" + b"1,2\n" * 600_000  # ~2.4 MB
        res = c.post("/api/sources/upload", data={"name": "big"}, files=[("files", ("big.csv", big, "text/csv"))])
        assert res.status_code == 413


def test_ai_details_only_for_the_people_who_run_it(hosted):
    app, _ = hosted
    c, _ = join(app)
    llm = c.get("/api/health").json()["llm"]
    assert llm["reachable"] is False and "base_url" not in llm and "error" not in llm  # a workspace admin
    ops, _ = join(app, "ops@stardeck.app")
    assert "base_url" in ops.get("/api/health").json()["llm"]  # the operator
    assert browser(app).get("/api/ping").json() == {"ok": True}  # no sign-in needed


def test_self_hosted_admins_see_ai_details(client):
    assert "base_url" in client.get("/api/health").json()["llm"]


def test_no_duckdb_files_either(hosted, tmp_path):
    app, _ = hosted
    c, _ = join(app)
    res = c.post("/api/sources", json={"name": "x", "config": {"kind": "duckdb", "path": str(tmp_path / "a.duckdb")}})
    assert res.status_code == 400 and "Upload" in res.json()["detail"]
