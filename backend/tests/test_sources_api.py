import sqlite3

import httpx
import yaml

from tests.conftest import fake_ai


def test_demo_source_is_seeded_with_flagged_column(client, demo_db):
    sources = client.get("/api/sources").json()
    assert [s["id"] for s in sources] == ["retail-demo"]
    demo = sources[0]
    orders = next(t for t in demo["tables"] if t["name"] == "orders")
    sts = next(c for c in orders["columns"] if c["name"] == "sts")
    assert sts["flag"] and sts["definition"] is None
    sgmt = next(c for t in demo["tables"] for c in t["columns"] if c["name"] == "sgmt")
    assert sgmt["definition"]["values"]["2"] == "Wholesale"
    assert sgmt["source"] == "user"
    assert orders["rowCount"] == demo_db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def test_save_column_definition_clears_flag(client):
    body = {"definition": {"description": "Order status", "values": {"1": "Completed"}}, "source": "chat"}
    out = client.put("/api/sources/retail-demo/columns/orders/sts", json=body).json()
    sts = next(c for t in out["tables"] if t["name"] == "orders" for c in t["columns"] if c["name"] == "sts")
    assert sts["flag"] is None and sts["source"] == "chat"
    assert sts["definition"] == {"description": "Order status", "values": {"1": "Completed"}}
    # Clearing it brings the flag back.
    out = client.put("/api/sources/retail-demo/columns/orders/sts", json={"definition": None}).json()
    sts = next(c for t in out["tables"] if t["name"] == "orders" for c in t["columns"] if c["name"] == "sts")
    assert sts["definition"] is None and sts["flag"]


def test_unknown_column_is_404(client):
    assert client.put("/api/sources/retail-demo/columns/orders/nope", json={"definition": None}).status_code == 404


def test_context_glossary_and_yaml_roundtrip(client):
    client.put("/api/sources/retail-demo/context",
               json={"description": "Shop data", "glossary": [{"term": "gmv", "definition": "Gross value"}]})
    exported = client.get("/api/sources/retail-demo/definitions.yaml").text
    data = yaml.safe_load(exported)
    assert data["database"]["description"] == "Shop data"
    assert "source" not in str(data)  # provenance is internal
    data["database"]["description"] = "Edited"
    out = client.put("/api/sources/retail-demo/definitions.yaml",
                     files={"file": ("d.yaml", yaml.safe_dump(data))}).json()
    assert out["databaseDescription"] == "Edited"
    assert client.put("/api/sources/retail-demo/definitions.yaml",
                      files={"file": ("d.yaml", "- not a mapping")}).status_code == 400


def test_add_sqlite_source_and_delete(client, tmp_path):
    path = tmp_path / "shop.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sales (c1 INTEGER, region TEXT)")
    db.execute("INSERT INTO sales VALUES (5, 'North')")
    db.commit()
    db.close()
    cfg = {"kind": "sqlite", "path": str(path)}
    assert client.post("/api/sources/test", json={"name": "x", "config": cfg}).json()["ok"] is True
    created = client.post("/api/sources", json={"name": "Shop", "config": cfg})
    assert created.status_code == 201
    src = created.json()
    assert src["tables"][0]["columns"][0]["flag"] == "Generic name gives no hint of meaning"
    assert client.delete(f"/api/sources/{src['id']}").status_code == 204
    assert client.get(f"/api/sources/{src['id']}").status_code == 404


def test_bad_connection_is_reported_not_raised(client, tmp_path):
    cfg = {"kind": "sqlite", "path": str(tmp_path / "missing.sqlite")}
    res = client.post("/api/sources/test", json={"name": "x", "config": cfg}).json()
    assert res == {"ok": False, "message": f"File not found: {cfg['path']}", "tables": None}
    assert client.post("/api/sources", json={"name": "x", "config": cfg}).status_code == 400


def test_password_is_encrypted_and_never_returned(client, settings):
    # Postgres on a closed port fails to connect, so store directly via the service.
    from stardeck.connectors import PostgresConfig
    state = client.app.state.stardeck
    stored = state.sources._store_config(PostgresConfig(host="h", database="d", username="u", password="s3cret"))
    assert "password" not in stored and stored["password_enc"] != "s3cret"
    assert "s3cret" not in str(client.get("/api/sources").json())


def test_upload_csv_source(client):
    files = [("files", ("leads.csv", b"name,score\nA,1\nB,2\n", "text/csv"))]
    res = client.post("/api/sources/upload", data={"name": "Leads"}, files=files)
    assert res.status_code == 201, res.text
    src = res.json()
    assert src["kind"] == "csv" and src["tables"][0]["name"] == "leads"
    assert src["tables"][0]["rowCount"] == 2


def test_ai_suggestions_are_saved_for_review(client):
    reply = ('{"columns": [{"table": "orders", "column": "sts", "description": "Order status",'
             ' "values": [{"code": "1", "meaning": "Completed"}], "confidence": "medium"},'
             ' {"table": "orders", "column": "ghost", "description": "x", "confidence": "high"}]}')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    stub = fake_ai(client.app, handler)
    stub.__enter__()
    try:
        res = client.post("/api/sources/retail-demo/suggest").json()
    finally:
        stub.__exit__(None, None, None)
    assert res["suggested"] == 1  # the made-up column is ignored
    sts = next(c for t in res["source"]["tables"] if t["name"] == "orders" for c in t["columns"] if c["name"] == "sts")
    assert sts["source"] == "ai_suggested" and sts["definition"]["values"] == {"1": "Completed"}


def test_suggest_without_model_is_503(client):
    assert client.post("/api/sources/retail-demo/suggest").status_code == 503
