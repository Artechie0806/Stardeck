"""Public share links and the demo: what anyone on the internet can reach."""

import json
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from stardeck import admin, shares
from stardeck.api import public
from stardeck.config import Settings
from stardeck.db import ShareLinkRecord, utcnow
from stardeck.geo import Geocoder
from stardeck.invites import Invites
from stardeck.main import create_app
from stardeck.ratelimit import RateLimiter

APP_URL = "https://app.example.com"
PASSWORD = "public-pass-123"
CITY = {"id": "city", "table": "customers", "column": "city", "label": "City"}


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(public, "limits", RateLimiter())


@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", hosted=True,
                        public_url=APP_URL, share_url="https://share.example.com", site_url="https://example.com")
    application = create_app(settings)
    with TestClient(application, base_url=APP_URL):
        yield application


def member(app, plan: str = "community", email: str = "asha@acme.in") -> tuple[TestClient, str]:
    state = app.state.stardeck
    ws = state.create_workspace("Acme", plan)
    token = Invites(state.db).create(ws.id, email)
    c = TestClient(app, base_url=APP_URL)
    assert c.post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 200
    return c, ws.id


def board(c: TestClient) -> dict:
    """The sample dashboard, with a saved City filter."""
    dash = c.get("/api/dashboards").json()[0]
    full = c.get(f"/api/dashboards/{dash['id']}").json()
    res = c.patch(f"/api/dashboards/{dash['id']}", json={"filters": [{**CITY, "sourceId": full["sourceId"]}]})
    assert res.status_code == 200, res.text
    return res.json()


def anon(app) -> TestClient:
    return TestClient(app, base_url="https://share.example.com")


def lines(res) -> list[dict]:
    return [json.loads(x) for x in res.text.strip().splitlines()]


def widget(d: dict, title: str) -> str:
    return next(w["id"] for w in d["widgets"] if w["title"] == title)


def test_a_link_shows_the_dashboard_without_sql_or_sign_in(app):
    c, _ = member(app)
    d = board(c)
    link = c.post(f"/api/dashboards/{d['id']}/shares", json={}).json()
    assert link["active"] and link["expiresAt"] is None and len(link["token"]) >= 32
    out = anon(app)
    res = out.get(f"/api/public/s/{link['token']}")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == d["name"] and len(body["widgets"]) == len(d["widgets"])
    assert "SELECT" not in res.text and all("sql" not in w for w in body["widgets"])
    assert body["share"] == {"badge": True, "demo": False, "siteUrl": "https://example.com"}  # Community
    assert body["filters"][0]["id"] == "city" and body["config"]["fiscalYearStart"] == 4
    run = lines(out.post(f"/api/public/s/{link['token']}/run", json={}))
    assert run[-1] == {"done": True} and {r["id"] for r in run[:-1]} == {w["id"] for w in d["widgets"]}
    assert all("result" in r for r in run[:-1])
    assert "SELECT" not in json.dumps(run)
    assert c.get(f"/api/dashboards/{d['id']}/shares").json()[0]["views"] == 1
    assert c.get("/api/config").json()["shareUrl"] == "https://share.example.com"


def test_only_saved_filters_and_the_dashboards_own_charts_filter(app):
    c, _ = member(app)
    d = board(c)
    token = c.post(f"/api/dashboards/{d['id']}/shares", json={}).json()["token"]
    out = anon(app)
    kpi = widget(d, "Revenue")

    def revenue(**body) -> float:
        rows = {r["id"]: r for r in lines(out.post(f"/api/public/s/{token}/run", json=body))[:-1]}
        return rows[kpi]["result"]["rows"][0][0]

    everything = revenue()
    pune = revenue(filters=[{"id": "city", "values": ["Pune"]}])
    assert 0 < pune < everything
    category, city = widget(d, "Revenue by product category"), widget(d, "Revenue by city")
    assert revenue(picks=[{"widgetId": city, "column": "city", "value": "Pune"}]) == pune
    # Anything else is refused: unknown filters, untraceable columns, other charts, raw SQL bits.
    bad = [{"filters": [{"id": "nope", "values": ["x"]}]},
           {"picks": [{"widgetId": category, "column": "revenue", "value": 1}]},
           {"picks": [{"widgetId": "w-elsewhere", "column": "category", "value": "Home"}]}]
    for body in bad:
        assert out.post(f"/api/public/s/{token}/run", json=body).status_code == 400, body
    sneaky = {"filters": [{"id": "city", "values": ["Pune"], "table": "users", "column": "password_hash",
                           "expr": "1=1 OR 1"}]}
    assert revenue(**sneaky) == pune  # the extra keys are ignored: the saved filter's own column is used
    assert out.post(f"/api/public/s/{token}/run", json={"filters": [{"id": "city", "values": [{"$gt": ""}]}]}).status_code == 422
    # Clicking: only charts that can filter.
    click = out.post(f"/api/public/s/{token}/crossfilter", json={"widgetId": category, "column": "category", "value": "Home"})
    assert click.status_code == 200 and click.json()["key"] == f"x:{category}" and "expr" not in click.json()
    assert out.post(f"/api/public/s/{token}/crossfilter", json={"widgetId": kpi, "column": "revenue"}).status_code == 422
    # A saved filter's choices; nothing else.
    values = out.get(f"/api/public/s/{token}/filter-values", params={"filter": "city"}).json()
    assert "Pune" in values["values"]
    assert out.get(f"/api/public/s/{token}/filter-values", params={"filter": "nope"}).status_code == 404


def test_turned_off_expired_and_suspended_links_stop_working(app):
    c, ws = member(app)
    d = board(c)
    token = c.post(f"/api/dashboards/{d['id']}/shares", json={}).json()["token"]
    out = anon(app)
    assert out.get(f"/api/public/s/{token}").status_code == 200
    assert c.delete(f"/api/dashboards/{d['id']}/shares/{token}").status_code == 204
    for res in (out.get(f"/api/public/s/{token}"), out.post(f"/api/public/s/{token}/run", json={}),
                out.get(f"/api/public/s/{token}/filter-values", params={"filter": "city"}),
                out.post(f"/api/public/s/{token}/labels", json={"labels": ["x"]})):
        assert res.status_code == 404 and "doesn't work any more" in res.json()["detail"]
    assert c.get(f"/api/dashboards/{d['id']}/shares").json() == []

    later = c.post(f"/api/dashboards/{d['id']}/shares", json={"expiresInDays": 7}).json()
    assert out.get(f"/api/public/s/{later['token']}").status_code == 200
    state = app.state.stardeck
    with state.db.platform_session() as s:
        s.get(ShareLinkRecord, later["token"]).expires_at = utcnow() - timedelta(minutes=1)
        s.commit()
    assert out.get(f"/api/public/s/{later['token']}").status_code == 404

    third = c.post(f"/api/dashboards/{d['id']}/shares", json={}).json()["token"]
    state.workspaces.update(ws, status="suspended")
    assert out.get(f"/api/public/s/{third}").status_code == 404
    assert out.get("/api/public/s/not-a-real-token").status_code == 404


def test_viewers_cant_make_links_and_links_belong_to_their_workspace(app):
    c, ws = member(app, "enterprise")
    d = board(c)
    state = app.state.stardeck
    with state.db.new_session(ws) as db:
        state.users.create(db, "ravi@acme.in", "Ravi", PASSWORD, "viewer")
    v = TestClient(app, base_url=APP_URL)
    assert v.post("/api/auth/login", json={"username": "ravi@acme.in", "password": PASSWORD}).status_code == 200
    assert v.post(f"/api/dashboards/{d['id']}/shares", json={}).status_code == 403
    assert v.get(f"/api/dashboards/{d['id']}/shares").status_code == 403
    token = c.post(f"/api/dashboards/{d['id']}/shares", json={}).json()["token"]
    assert anon(app).get(f"/api/public/s/{token}").json()["share"]["badge"] is False  # Enterprise
    # Another workspace's charts mean nothing to this link (sample charts share ids, so add one there).
    other, _ = member(app, "enterprise", "zoe@zeta.in")
    theirs = board(other)
    chart = {"title": "Zeta cities", "sql": "SELECT c.city, COUNT(*) AS n FROM customers c GROUP BY c.city",
             "spec": {"type": "bar", "x": "city", "y": ["n"]}}
    added = other.post(f"/api/dashboards/{theirs['id']}/widgets", json=chart).json()
    pick = {"widgetId": widget(added, "Zeta cities"), "column": "city", "value": "Pune"}
    assert anon(app).post(f"/api/public/s/{token}/run", json={"picks": [pick]}).status_code == 400
    assert anon(app).post(f"/api/public/s/{token}/crossfilter", json=pick).status_code == 404


def test_no_ai_and_no_new_map_lookups(app, monkeypatch):
    c, _ = member(app)
    token = c.post(f"/api/dashboards/{board(c)['id']}/shares", json={}).json()["token"]
    looked_up = []
    monkeypatch.setattr(Geocoder, "_lookup", lambda self, place: looked_up.append(place) or (1.0, 2.0))
    out = anon(app)
    geo = out.post(f"/api/public/s/{token}/geocode", json={"places": ["Pune", "Atlantis"]}).json()
    assert geo == {"places": {}, "pending": 0} and looked_up == []
    labels = out.post(f"/api/public/s/{token}/labels", json={"labels": ["Electronics and accessories"], "maxChars": 12})
    assert labels.status_code == 200 and labels.json()["ai"] is False
    assert len(labels.json()["labels"]["Electronics and accessories"]) <= 12


def test_rate_limits_and_the_cap_on_runs_at_once(app, monkeypatch):
    c, _ = member(app)
    token = c.post(f"/api/dashboards/{board(c)['id']}/shares", json={}).json()["token"]
    out = anon(app)
    monkeypatch.setattr(public, "RUNS_PER_IP", (2, 60))
    assert out.post(f"/api/public/s/{token}/run", json={}).status_code == 200
    assert out.post(f"/api/public/s/{token}/run", json={}).status_code == 200
    third = out.post(f"/api/public/s/{token}/run", json={})
    assert third.status_code == 429 and "retry-after" in third.headers
    monkeypatch.setattr(public, "limits", RateLimiter())
    busy = threading.BoundedSemaphore(1)
    busy.acquire()  # someone else's run holds the only slot
    monkeypatch.setattr(public, "RUNS_AT_ONCE", busy)
    monkeypatch.setattr(public, "RUN_WAIT_S", 0.2)
    res = out.post(f"/api/public/s/{token}/run", json={})
    assert res.status_code == 503 and res.headers["retry-after"] == "15"


def test_public_calls_dont_need_the_apps_origin(app):
    """The share host is another origin; public calls never act as the signed-in person anyway."""
    c, _ = member(app)
    token = c.post(f"/api/dashboards/{board(c)['id']}/shares", json={}).json()["token"]
    res = anon(app).post(f"/api/public/s/{token}/run", json={}, headers={"Origin": "https://share.example.com"})
    assert res.status_code == 200
    assert c.post("/api/dashboards", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_demo_setup_and_its_public_link(app, capsys):
    out = anon(app)
    assert out.get("/api/public/demo").status_code == 404
    state = app.state.stardeck
    token = admin.demo_setup(state)
    assert "https://share.example.com/s/" in capsys.readouterr().out
    assert admin.demo_setup(state) == token  # running it again keeps the link
    assert out.get("/api/public/demo").json() == {"token": token}
    body = out.get(f"/api/public/s/{token}").json()
    assert body["share"]["demo"] is True and [f["id"] for f in body["filters"]] == ["city", "category"]
    values = out.get(f"/api/public/s/{token}/filter-values", params={"filter": "category"}).json()["values"]
    assert {"Electronics", "Home"} <= set(values)
    admin.share_revoke(state, token)
    assert out.get("/api/public/demo").status_code == 404


def test_deleting_a_workspace_removes_its_links(app):
    c, ws = member(app)
    token = c.post(f"/api/dashboards/{board(c)['id']}/shares", json={}).json()["token"]
    admin.workspace_delete(app.state.stardeck, ws)
    with app.state.stardeck.db.platform_session() as s:
        assert s.get(ShareLinkRecord, token) is None


def test_a_dashboard_has_ten_links_at_most(app):
    c, _ = member(app)
    d = board(c)
    for _ in range(shares.MAX_PER_DASHBOARD):
        assert c.post(f"/api/dashboards/{d['id']}/shares", json={}).status_code == 201
    res = c.post(f"/api/dashboards/{d['id']}/shares", json={})
    assert res.status_code == 400 and "Turn one off" in res.json()["detail"]
