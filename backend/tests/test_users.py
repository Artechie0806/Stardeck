"""Sign-in with accounts, roles, and row-level security for viewers."""

import pytest
from fastapi.testclient import TestClient

ADMIN = {"username": "asha", "name": "Asha", "password": "admin-pass-1"}
VIEWER = {"username": "ravi", "name": "Ravi", "password": "viewer-pass-1", "role": "viewer",
          "rules": [{"sourceId": "retail-demo", "table": "customers", "column": "city", "values": ["Pune"]}]}


@pytest.fixture
def admin(client):
    """Turn on sign-in by creating the first admin (the open-mode client becomes that admin)."""
    res = client.post("/api/auth/setup", json=ADMIN)
    assert res.status_code == 200, res.text
    return client


@pytest.fixture
def viewer(admin):
    assert admin.post("/api/users", json=VIEWER).status_code == 201
    v = TestClient(admin.app)
    assert v.post("/api/auth/login", json={"username": "ravi", "password": VIEWER["password"]}).status_code == 200
    return v


def test_setup_switches_sign_in_on(admin):
    status = admin.get("/api/auth/status").json()
    assert status["mode"] == "users" and status["user"]["role"] == "admin"
    stranger = TestClient(admin.app)
    assert stranger.get("/api/dashboards").status_code == 401
    assert stranger.post("/api/auth/setup", json={**ADMIN, "username": "eve"}).status_code == 409
    assert stranger.post("/api/auth/login", json={"username": "asha", "password": "wrong-pass"}).status_code == 401


def test_viewers_can_look_but_not_change(viewer):
    assert viewer.get("/api/dashboards").status_code == 200
    widget = {"title": "x", "sql": "SELECT 1 AS x", "spec": {"type": "kpi"}}
    assert viewer.post("/api/dashboards/sales-overview/widgets", json=widget).status_code == 403
    assert viewer.delete("/api/dashboards/sales-overview").status_code == 403
    assert viewer.get("/api/users").status_code == 403
    assert viewer.post("/api/sources/retail-demo/query", json={"sql": "SELECT 1"}).status_code == 403
    assert viewer.post("/api/sources/retail-demo/transforms/preview", json={"base": "orders"}).status_code == 403
    src = viewer.get("/api/sources/retail-demo").json()
    assert src["tables"] == [] and src["config"] == {}  # no schema, samples or connection details


def test_viewers_only_see_their_rows(viewer, admin, demo_db):
    pune = demo_db.execute("SELECT SUM(o.amt) / 100.0 FROM orders o JOIN customers c ON c.id = o.customer_id "
                           "WHERE o.sts = 1 AND c.city = 'Pune'").fetchone()[0]
    everyone = demo_db.execute("SELECT SUM(amt) / 100.0 FROM orders WHERE sts = 1").fetchone()[0]
    mine = viewer.post("/api/dashboards/sales-overview/run", json={}).json()
    theirs = admin.post("/api/dashboards/sales-overview/run", json={}).json()
    assert mine["kpi_revenue"]["result"]["rows"][0][0] == pytest.approx(pune)
    assert theirs["kpi_revenue"]["result"]["rows"][0][0] == pytest.approx(everyone)
    cities = {r[0] for r in mine["revenue_by_city"]["result"]["rows"]}
    assert cities == {"Pune"}
    values = viewer.get("/api/dashboards/sales-overview/filter-values",
                        params={"source_id": "retail-demo", "table": "customers", "column": "city"}).json()
    assert values["values"] == ["Pune"]


def test_rules_only_on_real_columns(admin):
    bad = {**VIEWER, "username": "x1", "rules": [{"sourceId": "retail-demo", "table": "customers",
                                                  "column": "nope", "values": ["a"]}]}
    assert "no column nope" in admin.post("/api/users", json=bad).json()["detail"]
    short = {**VIEWER, "username": "x2", "password": "short"}
    assert "at least 8" in admin.post("/api/users", json=short).json()["detail"]


def test_admin_guard_rails_and_disabling(admin, viewer):
    me = admin.get("/api/auth/status").json()["user"]
    assert "own admin access" in admin.patch(f"/api/users/{me['id']}", json={"role": "viewer"}).json()["detail"]
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400
    ravi = next(u for u in admin.get("/api/users").json() if u["username"] == "ravi")
    assert admin.patch(f"/api/users/{ravi['id']}", json={"disabled": True}).status_code == 200
    assert viewer.get("/api/dashboards").status_code == 401  # signed out at once


def test_coverage_shows_what_a_rule_reaches(admin):
    cov = admin.get("/api/sources/retail-demo/coverage", params={"table": "customers"}).json()
    assert set(cov["restricted"]) == {"customers", "orders", "order_items"} and cov["unrestricted"] == ["products"]
