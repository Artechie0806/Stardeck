"""Community and Enterprise: what each plan allows, enforced by the server."""

import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from stardeck.config import Settings
from stardeck.db import SourceRecord
from stardeck.invites import Invites
from stardeck.main import create_app
from tests.conftest import fake_ai

APP_URL = "https://app.example.com"
PASSWORD = "plans-pass-1234"
CSV = b"city,target\nPune,100\nDelhi,200\n"
MEASURE = {"measure": {"name": "rev", "table": "orders", "expression": "SUM(amt)"}}


@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", hosted=True,
                        public_url=APP_URL, upgrade_url="https://stardeck.example/pricing")
    application = create_app(settings)
    with TestClient(application, base_url=APP_URL):
        yield application


def member(app, plan: str, email: str = "asha@acme.in") -> tuple[TestClient, str]:
    state = app.state.stardeck
    ws = state.create_workspace("Acme", plan)
    token = Invites(state.db).create(ws.id, email)
    c = TestClient(app, base_url=APP_URL)
    assert c.post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 200
    return c, ws.id


def upload(c: TestClient, name: str = "Targets"):
    return c.post("/api/sources/upload", data={"name": name}, files=[("files", ("t.csv", CSV, "text/csv"))])


def sample_id(c: TestClient) -> str:
    return next(s["id"] for s in c.get("/api/sources").json() if s["sample"])


def test_config_and_usage_describe_the_plan(app):
    c, _ = member(app, "community")
    cfg = c.get("/api/config").json()
    assert cfg["plan"]["id"] == "community" and cfg["plan"]["features"] == [] and cfg["plan"]["badge"] is True
    assert cfg["plan"]["limits"]["sources"] == 1 and cfg["upgradeUrl"] == "https://stardeck.example/pricing"
    usage = c.get("/api/usage").json()
    assert (usage["sources"], usage["dashboards"]) == ({"used": 0, "limit": 1}, {"used": 0, "limit": 2})
    assert usage["ai"]["generation"] == {"used": 0, "limit": 3} and usage["ai"]["assistant"] == {"used": 0, "limit": 5}


def test_community_gets_one_data_source(app):
    c, _ = member(app, "community")
    assert upload(c).status_code == 201  # the sample doesn't count
    second = upload(c, "More")
    assert second.status_code == 402 and "1 data source" in second.json()["detail"]
    db_source = {"name": "DB", "config": {"kind": "postgres", "host": "db.example.com", "database": "d", "username": "u"}}
    assert c.post("/api/sources", json=db_source).status_code == 402  # refused before connecting anywhere
    assert c.get("/api/usage").json()["sources"] == {"used": 1, "limit": 1}


def test_community_gets_two_dashboards(app):
    c, _ = member(app, "community")
    src = sample_id(c)
    for _ in range(2):
        assert c.post("/api/dashboards", json={"name": "Mine", "sourceIds": [src]}).status_code == 201
    third = c.post("/api/dashboards", json={"name": "Third", "sourceIds": [src]})
    assert third.status_code == 402 and "2 dashboards" in third.json()["detail"]
    assert c.post("/api/dashboards/generate", json={"sourceIds": [src]}).status_code == 402  # before any AI call


def test_community_uses_one_source_per_dashboard(app):
    c, _ = member(app, "community")
    src, other = sample_id(c), upload(c).json()["id"]
    assert c.post("/api/dashboards", json={"name": "x", "sourceIds": [src, other]}).status_code == 402
    assert c.post("/api/dashboards/generate", json={"sourceIds": [src, other]}).status_code == 402
    dash = next(d for d in c.get("/api/dashboards").json())
    assert c.patch(f"/api/dashboards/{dash['id']}", json={"sourceIds": [src, other]}).status_code == 402
    widget = {"sourceId": other, "title": "t", "sql": "SELECT city FROM t", "spec": {"type": "table"}}
    assert c.post(f"/api/dashboards/{dash['id']}/widgets", json=widget).status_code == 402
    same = {"sourceId": src, "title": "Orders", "sql": "SELECT COUNT(*) AS n FROM orders", "spec": {"type": "kpi"}}
    assert c.post(f"/api/dashboards/{dash['id']}/widgets", json=same).status_code == 201


def test_enterprise_features_are_locked_on_community(app):
    c, _ = member(app, "community")
    src = sample_id(c)
    locked = [
        c.get("/api/users"),
        c.post("/api/users", json={"username": "ravi@acme.in", "password": PASSWORD}),
        c.get(f"/api/sources/{src}/coverage", params={"table": "customers"}),
        c.post(f"/api/sources/{src}/measures/check", json=MEASURE),
        c.put(f"/api/sources/{src}/measures/rev", json=MEASURE),
        c.post(f"/api/sources/{src}/measures/suggest"),
        c.post(f"/api/sources/{src}/relationships", json={"from": "orders.customer_id", "to": "customers.id"}),
        c.put(f"/api/sources/{src}/relationships", json={"key": "orders.customer_id->customers.id", "ignored": True}),
        c.put(f"/api/sources/{src}/transforms/clean", json={"base": "orders", "steps": []}),
        c.post(f"/api/sources/{src}/transforms/suggest", json={"base": "orders", "request": "only completed"}),
    ]
    for res in locked:
        assert res.status_code == 402, res.request.url
        assert "Stardeck Enterprise" in res.json()["detail"]
    # Looking is fine: definitions, prepared-table previews, column values.
    assert c.get(f"/api/sources/{src}/transforms").status_code == 200
    assert c.post(f"/api/sources/{src}/transforms/preview", json={"base": "orders", "steps": []}).status_code == 200
    assert c.put(f"/api/sources/{src}/columns/orders/sts", json={"definition": {"description": "Status"}}).status_code == 200


def test_definitions_files_cant_bring_in_locked_features(app):
    c, _ = member(app, "community")
    src = sample_id(c)
    current = c.get(f"/api/sources/{src}/definitions.yaml").content
    assert c.put(f"/api/sources/{src}/definitions.yaml", files={"file": ("d.yaml", io.BytesIO(current))}).status_code == 200
    with_measure = current + b"\nmeasures:\n- name: rev\n  table: orders\n  expression: SUM(amt)\n"
    res = c.put(f"/api/sources/{src}/definitions.yaml", files={"file": ("d.yaml", io.BytesIO(with_measure))})
    assert res.status_code == 402


def test_enterprise_has_everything(app):
    c, _ = member(app, "enterprise")
    src = sample_id(c)
    assert c.get("/api/users").status_code == 200
    assert c.post(f"/api/sources/{src}/measures/check", json=MEASURE).status_code == 200
    assert upload(c).status_code == 201 and upload(c, "Two").status_code == 201
    other = upload(c, "Three").json()["id"]
    assert c.post("/api/dashboards", json={"name": "x", "sourceIds": [src, other]}).status_code == 201
    assert c.get("/api/config").json()["plan"]["badge"] is False


def test_operator_exceptions_and_downgrades(app):
    c, ws = member(app, "enterprise")
    state = app.state.stardeck
    src = sample_id(c)
    assert c.put(f"/api/sources/{src}/measures/rev", json=MEASURE).status_code == 200
    dash = c.post("/api/dashboards", json={"name": "Revenue", "sourceIds": [src], "widgets": [
        {"title": "Revenue", "sql": "SELECT MEASURE(rev) AS r FROM orders", "spec": {"type": "kpi"}}]}).json()

    state.workspaces.update(ws, plan="community")  # downgrade: what exists keeps working, read-only
    run = c.post(f"/api/dashboards/{dash['id']}/run", json={}).json()
    assert "result" in run[dash["widgets"][0]["id"]]
    assert c.put(f"/api/sources/{src}/measures/rev", json=MEASURE).status_code == 402
    assert c.delete(f"/api/sources/{src}/measures/rev").status_code == 200  # tidying up is allowed

    state.workspaces.update(ws, limits_override={"sources": 2, "features": ["measures"]})  # an exception
    assert upload(c).status_code == 201 and upload(c, "Two").status_code == 201
    assert upload(c, "Three").status_code == 402
    assert c.post(f"/api/sources/{src}/measures/check", json=MEASURE).status_code == 200
    assert c.get("/api/users").status_code == 402


def test_upload_size_follows_the_plan(app):
    c, ws = member(app, "community")
    app.state.stardeck.workspaces.update(ws, limits_override={"upload_mb": 1})
    big = b"a,b\n" + b"1,2\n" * 400_000  # ~1.6 MB: under the server's limit, over this plan's
    res = c.post("/api/sources/upload", data={"name": "big"}, files=[("files", ("big.csv", big, "text/csv"))])
    assert res.status_code == 413 and "1 MB" in res.json()["detail"]


def test_generation_rechecks_the_allowance_before_saving(app):
    """A dashboard saved by someone else while the AI was building this one fills the last slot."""
    c, ws = member(app, "community")
    state, src = app.state.stardeck, sample_id(c)
    assert c.post("/api/dashboards", json={"name": "First", "sourceIds": [src]}).status_code == 201
    plan = {"reasoning": "r", "name": "Orders", "description": "d", "widgets": [
        {"title": "Orders", "request": "number of orders", "kind": "kpi"},
        {"title": "Orders 2", "request": "number of orders", "kind": "kpi"},
        {"title": "Orders 3", "request": "number of orders", "kind": "kpi"}]}
    draft = {"reasoning": "r", "title": "t", "summary": "s.", "chart_type": "kpi", "sql": "SELECT COUNT(*) AS n FROM orders"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "DashboardPlan":
            with state.db.new_session(ws) as db:  # meanwhile, the last slot is taken
                state.dashboards.create(db, "Second", "", [src], [])
            reply = plan
        else:
            reply = draft
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    with fake_ai(app, handler):
        res = c.post("/api/dashboards/generate", json={"sourceIds": [src]})
    last = json.loads(res.text.strip().splitlines()[-1])
    assert last["type"] == "error" and "2 dashboards" in last["message"]
    assert c.get("/api/usage").json()["dashboards"]["used"] == 2


def test_self_hosted_is_unlimited(client):
    for name in ("one", "two", "three"):
        assert upload(client, name).status_code == 201
    assert client.get("/api/config").json()["plan"]["id"] == "self_hosted"
    with client.app.state.stardeck.db.new_session("default") as db:
        assert db.query(SourceRecord).count() == 4
