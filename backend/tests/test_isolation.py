"""Workspaces can't see or change each other's data, through any route or any query shape."""

import hashlib
import io
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exists, func, select

from stardeck import shares
from stardeck.config import Settings
from stardeck.db import DashboardRecord, ScopeError, SourceRecord, UserRecord
from stardeck.main import create_app

PASSWORD = "isolation-pass-1"
CSV = b"city,target\nPune,100\nDelhi,200\n"


@pytest.fixture
def world(tmp_path):
    """Two Enterprise workspaces (so plan limits can't mask a missing 404), each with an admin, a
    viewer, the sample source and dashboard, and an uploaded file. B's things are named "Bravo..."."""
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1",
                        seed_demo=False)
    app = create_app(settings)
    with TestClient(app) as main:
        state = app.state.stardeck
        out = {}
        for key in ("a", "b"):
            ws = state.create_workspace(f"Org {key}", plan="enterprise")
            with state.db.new_session(ws.id) as db:
                admin = state.users.create(db, f"admin-{key}", "Admin", PASSWORD, "admin")
                viewer = state.users.create(db, f"viewer-{key}", "Viewer", PASSWORD, "viewer")
                src, dash = db.query(SourceRecord).one(), db.query(DashboardRecord).one()
                if key == "b":
                    src.name, dash.name = "Bravo source", "Bravo board"
                    db.commit()
                link = shares.create(db, dash.id, state.users.to_principal(admin))
                out[key] = {"ws": ws.id, "admin": admin.id, "viewer": viewer.id, "src": src.id, "dash": dash.id,
                            "widget": dash.widgets[0]["id"], "token": link.token}
            c = TestClient(app)  # its own cookies; the app is already started by `main`
            assert c.post("/api/auth/login", json={"username": f"admin-{key}", "password": PASSWORD}).status_code == 200
            up = c.post("/api/sources/upload", data={"name": "Bravo upload" if key == "b" else "Upload"},
                        files=[("files", ("targets.csv", CSV, "text/csv"))])
            assert up.status_code == 201, up.text
            out[key]["upload"] = up.json()["id"]
            out[key]["client"] = c
        out["state"], out["app"], out["settings"] = state, app, settings
        yield out
        assert main  # keeps the app running for the test


def snapshot(state, settings, ws: str) -> tuple:
    """Everything workspace `ws` has: rows and uploaded files."""
    with state.db.new_session(ws) as db:
        rows = (
            sorted((s.id, s.name, repr(s.config), repr(s.definitions), repr(s.transforms)) for s in db.query(SourceRecord)),
            sorted((d.id, d.name, repr(d.widgets), repr(d.filters), repr(d.source_ids)) for d in db.query(DashboardRecord)),
            sorted((u.id, u.username, u.name, u.role, repr(u.rules), u.disabled, u.password_hash) for u in db.query(UserRecord)),
        )
    folder = settings.data_dir / "uploads" / ws
    files = sorted((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in folder.iterdir()) if folder.exists() else []
    return rows, files


def specs(b: dict) -> dict[tuple[str, str], dict]:
    """A minimal valid request for every route that takes an id, aimed at workspace B's records."""
    measure = {"measure": {"name": "rev", "table": "orders", "expression": "SUM(amt)"}}
    recipe = {"base": "orders", "steps": []}
    return {
        ("GET", "/api/sources/{source_id}"): {},
        ("PUT", "/api/sources/{source_id}"): {"json": {"name": "x", "config": {"kind": "sqlite", "path": "/etc/hostname"}}},
        ("DELETE", "/api/sources/{source_id}"): {},
        ("POST", "/api/sources/{source_id}/refresh"): {},
        ("PUT", "/api/sources/{source_id}/columns/{table}/{column}"): {"json": {"definition": None}},
        ("PUT", "/api/sources/{source_id}/tables/{table}"): {"json": {"description": "x"}},
        ("PUT", "/api/sources/{source_id}/context"): {"json": {"description": "x"}},
        ("POST", "/api/sources/{source_id}/suggest"): {},
        ("GET", "/api/sources/{source_id}/definitions.yaml"): {},
        ("PUT", "/api/sources/{source_id}/definitions.yaml"): {"files": {"file": ("d.yaml", io.BytesIO(b"glossary: []\n"))}},
        ("POST", "/api/sources/{source_id}/measures/check"): {"json": measure},
        ("PUT", "/api/sources/{source_id}/measures/{name}"): {"json": measure},
        ("DELETE", "/api/sources/{source_id}/measures/{name}"): {},
        ("POST", "/api/sources/{source_id}/measures/suggest"): {},
        ("POST", "/api/sources/{source_id}/relationships"): {"json": {"from": "orders.customer_id", "to": "customers.id"}},
        ("PUT", "/api/sources/{source_id}/relationships"): {"json": {"key": "orders.customer_id->customers.id", "ignored": True}},
        ("GET", "/api/sources/{source_id}/values"): {"params": {"table": "customers", "column": "city"}},
        ("GET", "/api/sources/{source_id}/coverage"): {"params": {"table": "customers"}},
        ("POST", "/api/sources/{source_id}/widgets/generate"): {"json": {"request": "revenue by month"}},
        ("POST", "/api/sources/{source_id}/query"): {"json": {"sql": "SELECT 1"}},
        ("GET", "/api/sources/{source_id}/transforms"): {},
        ("POST", "/api/sources/{source_id}/transforms/preview"): {"json": recipe},
        ("PUT", "/api/sources/{source_id}/transforms/{name}"): {"json": recipe},
        ("DELETE", "/api/sources/{source_id}/transforms/{name}"): {},
        ("POST", "/api/sources/{source_id}/transforms/distinct"): {"json": {**recipe, "upto": 0, "column": "sts"}},
        ("POST", "/api/sources/{source_id}/transforms/suggest"): {"json": {**recipe, "request": "only completed"}},
        ("GET", "/api/dashboards/{dashboard_id}"): {},
        ("PATCH", "/api/dashboards/{dashboard_id}"): {"json": {"name": "x"}},
        ("DELETE", "/api/dashboards/{dashboard_id}"): {},
        ("PUT", "/api/dashboards/{dashboard_id}/layout"): {"json": {}},
        ("POST", "/api/dashboards/{dashboard_id}/widgets"): {"json": {"title": "x", "sql": "SELECT 1", "spec": {"type": "kpi"}}},
        ("PATCH", "/api/dashboards/{dashboard_id}/widgets/{widget_id}"): {"json": {"title": "x"}},
        ("DELETE", "/api/dashboards/{dashboard_id}/widgets/{widget_id}"): {},
        ("POST", "/api/dashboards/{dashboard_id}/run"): {"json": {}},
        ("POST", "/api/dashboards/{dashboard_id}/crossfilter"): {"json": {"widgetId": b["widget"], "column": "x", "value": 1}},
        ("GET", "/api/dashboards/{dashboard_id}/filter-values"): {
            "params": {"source_id": b["src"], "table": "customers", "column": "city"}},
        ("POST", "/api/dashboards/{dashboard_id}/chat"): {"json": {"message": "hello"}},
        ("GET", "/api/dashboards/{dashboard_id}/shares"): {},
        ("POST", "/api/dashboards/{dashboard_id}/shares"): {"json": {}},
        ("DELETE", "/api/dashboards/{dashboard_id}/shares/{token}"): {},
        ("PATCH", "/api/users/{user_id}"): {"json": {"name": "x"}},
        ("DELETE", "/api/users/{user_id}"): {},
    }


def fill(path: str, b: dict) -> str:
    return (path.replace("{source_id}", b["src"]).replace("{dashboard_id}", b["dash"])
            .replace("{widget_id}", b["widget"]).replace("{user_id}", b["viewer"])
            .replace("{table}", "orders").replace("{column}", "amt").replace("{name}", "revenue")
            .replace("{token}", b["token"]))


def id_routes(app) -> set[tuple[str, str]]:
    """Every route with an id in its path. From the OpenAPI schema: since FastAPI 0.14x, `app.routes`
    holds included routers rather than their routes. Routes by token (share links, invitations) belong
    to no signed-in workspace: tests/test_public.py and tests/test_hosted.py cover them."""
    return {(m.upper(), path) for path, ops in app.openapi()["paths"].items()
            if "{" in path and not path.startswith(("/api/public/", "/api/auth/")) for m in ops}


def test_every_route_with_an_id_is_covered(world):
    """A new route that takes an id must be added to `specs`, so it can't skip the isolation check."""
    routes = id_routes(world["app"])
    assert len(routes) > 40  # the schema really lists them
    assert routes - set(specs(world["b"])) == set()


def test_no_route_reaches_another_workspace(world):
    a, b, state, settings = world["a"]["client"], world["b"], world["state"], world["settings"]
    before = snapshot(state, settings, b["ws"])
    for (method, path), kwargs in specs(b).items():
        res = a.request(method, fill(path, b), **kwargs)
        assert res.status_code == 404, f"{method} {path} -> {res.status_code} {res.text[:200]}"
        assert "Bravo" not in res.text, f"{method} {path} leaked B's data"
    assert snapshot(state, settings, b["ws"]) == before


def test_ids_inside_requests_are_checked(world):
    a, A, B = world["a"]["client"], world["a"], world["b"]
    assert a.post("/api/dashboards", json={"name": "x", "sourceIds": [B["src"]]}).status_code == 404
    assert a.patch(f"/api/dashboards/{A['dash']}", json={"sourceIds": [A["src"], B["src"]]}).status_code == 404
    assert a.post(f"/api/dashboards/{A['dash']}/widgets",
                  json={"sourceId": B["src"], "title": "x", "sql": "SELECT 1", "spec": {"type": "kpi"}}).status_code == 404
    bad_filter = {"id": "f1", "sourceId": B["src"], "table": "customers", "column": "city"}
    assert a.patch(f"/api/dashboards/{A['dash']}", json={"filters": [bad_filter]}).status_code == 400
    active = {"key": "f1", "sourceId": B["src"], "table": "customers", "column": "city", "values": ["Pune"]}
    assert a.post(f"/api/dashboards/{A['dash']}/run", json={"filters": [active]}).status_code == 400
    assert a.get(f"/api/dashboards/{A['dash']}/filter-values",
                 params={"source_id": B["src"], "table": "customers", "column": "city"}).status_code == 400
    rule = {"sourceId": B["src"], "table": "customers", "column": "city", "values": ["Pune"]}
    assert a.post("/api/users", json={"username": "new-person", "password": PASSWORD, "rules": [rule]}).status_code == 400
    assert a.post("/api/dashboards/generate", json={"sourceIds": [B["src"]]}).status_code == 404


def test_lists_only_show_your_own(world):
    a, A = world["a"]["client"], world["a"]
    assert {s["id"] for s in a.get("/api/sources").json()} == {A["src"], A["upload"]}
    assert {d["id"] for d in a.get("/api/dashboards").json()} == {A["dash"]}
    assert {u["id"] for u in a.get("/api/users").json()} == {A["admin"], A["viewer"]}


def test_uploads_live_in_each_workspace_folder(world):
    settings, A, B = world["settings"], world["a"], world["b"]
    assert (settings.data_dir / "uploads" / A["ws"] / f"{A['upload']}.sqlite").is_file()
    assert (settings.data_dir / "uploads" / B["ws"] / f"{B['upload']}.sqlite").is_file()


def test_settings_are_per_workspace(world):
    a, b = world["a"]["client"], world["b"]["client"]
    assert a.put("/api/settings", json={"fiscalYearStart": 1}).json()["fiscalYearStart"] == 1
    assert b.get("/api/config").json()["fiscalYearStart"] == 4


def test_sessions_are_scoped_whatever_the_query(world):
    state, A, B = world["state"], world["a"], world["b"]
    with state.db.new_session(A["ws"]) as db:
        assert db.get(SourceRecord, B["src"]) is None
        assert set(db.scalars(select(SourceRecord.id))) == {A["src"], A["upload"]}
        assert db.query(SourceRecord).count() == 2
        joined = db.query(DashboardRecord.id, SourceRecord.id).join(SourceRecord, SourceRecord.id == DashboardRecord.source_id).all()
        assert joined == [(A["dash"], A["src"])]
        inner = db.query(SourceRecord.id).filter(SourceRecord.id.in_(select(DashboardRecord.source_id))).all()
        assert inner == [(A["src"],)]
        # Shapes that hide their model from the statement's top level are filtered too.
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 2
        assert db.scalar(select(func.count()).select_from(select(SourceRecord.id).subquery())) == 2
        assert db.query(exists().where(SourceRecord.id == B["src"])).scalar() is False
        assert len(db.execute(select(SourceRecord.id).union(select(DashboardRecord.source_id))).all()) == 2
    with state.db.platform_session() as db:
        assert db.query(SourceRecord).count() == 4


def test_unscoped_and_cross_workspace_access_fails_loudly(world):
    state, A, B = world["state"], world["a"], world["b"]
    with state.db._sessions() as raw, pytest.raises(ScopeError):
        raw.query(SourceRecord).all()
    with pytest.raises(ScopeError):
        state.db.new_session("")
    with state.db.new_session(A["ws"]) as db:
        db.add(UserRecord(id="u-intruder", username="intruder", password_hash="x", role="admin", workspace_id=B["ws"]))
        with pytest.raises(ScopeError):
            db.commit()
    with state.db.platform_session() as plat:
        theirs = plat.get(SourceRecord, B["src"])
        plat.expunge(theirs)
    with state.db.new_session(A["ws"]) as db:
        mine = db.merge(theirs, load=False)
        mine.name = "changed"
        with pytest.raises(ScopeError):
            db.commit()


def test_concurrent_sessions_never_mix(world):
    state, A, B = world["state"], world["a"], world["b"]
    expected = {A["ws"]: {A["src"], A["upload"]}, B["ws"]: {B["src"], B["upload"]}}
    errors: list[str] = []

    def worker(ws: str) -> None:
        for _ in range(200):
            with state.db.new_session(ws) as db:
                got = set(db.scalars(select(SourceRecord.id)))
            if got != expected[ws]:
                errors.append(f"{ws}: {got}")

    threads = [threading.Thread(target=worker, args=(ws,)) for ws in expected for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
