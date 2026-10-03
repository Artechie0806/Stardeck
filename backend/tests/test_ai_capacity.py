"""One GPU, many people: the shared AI queue, daily allowances, and stopping work nobody waits for."""

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import stardeck.api.dashboards as api_dash
import stardeck.usage as usage_mod
from stardeck.config import Settings
from stardeck.invites import Invites
from stardeck.llm import LLMBusy, LLMCancelled, LLMClient, LLMUnavailable
from stardeck.llm.gate import Gate
from stardeck.llm.status import LLMStatus
from stardeck.main import create_app
from stardeck.usage import IST, QuotaExceeded
from tests.conftest import fake_ai

APP_URL = "https://app.example.com"
PASSWORD = "capacity-pass-1"


# ---------- the queue ----------

def test_never_more_than_the_slots_at_once():
    gate, busy, peak, lock = Gate(3, max_waiting=12), [0], [0], threading.Lock()  # room for everyone to wait

    def use(background: bool) -> None:
        with gate.slot(background, wait_s=5):
            with lock:
                busy[0] += 1
                peak[0] = max(peak[0], busy[0])
            time.sleep(0.05)
            with lock:
                busy[0] -= 1

    threads = [threading.Thread(target=use, args=(i % 2 == 0,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 3


def test_background_work_never_takes_the_last_slot():
    gate = Gate(2)  # one for background work, one kept for chat
    with gate.slot(background=True):
        with pytest.raises(LLMBusy), gate.slot(background=True, wait_s=0.2):
            pass
        with gate.slot(background=False, wait_s=0.2):  # chat still gets in
            assert gate.stats["active"] == 2


def test_a_full_queue_says_busy_at_once_and_waits_are_bounded():
    gate = Gate(1, max_waiting=1)
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with gate.slot():
            held.set()
            release.wait(5)

    def wait() -> None:
        try:
            with gate.slot(wait_s=5):
                pass
        except LLMBusy:
            pass

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(5)
    waiter = threading.Thread(target=wait)
    waiter.start()
    time.sleep(0.1)  # the waiter is now queued
    start = time.monotonic()
    with pytest.raises(LLMBusy), gate.slot(wait_s=5):
        pass
    assert time.monotonic() - start < 0.1  # queue full: no waiting at all
    release.set()
    holder.join()
    waiter.join()
    with gate.slot():
        start = time.monotonic()
        with pytest.raises(LLMBusy), gate.slot(wait_s=0.3):
            pass
        assert 0.25 < time.monotonic() - start < 1.0


def test_cancelled_waits_stop():
    gate, cancel = Gate(1), threading.Event()
    with gate.slot():
        threading.Timer(0.2, cancel.set).start()
        with pytest.raises(LLMCancelled), gate.slot(wait_s=5, cancel=cancel):
            pass


def test_an_offline_ai_fails_at_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused")

    status = LLMStatus(Settings(_env_file=None))
    client = LLMClient(Settings(_env_file=None), transport=httpx.MockTransport(handler), status=status)
    with pytest.raises(Exception):  # noqa: B017 - the first call finds out
        client.chat([{"role": "user", "content": "hi"}])
    assert status.offline() and len(calls) == 1
    start = time.monotonic()
    with pytest.raises(LLMUnavailable):
        client.chat([{"role": "user", "content": "hi"}])
    assert time.monotonic() - start < 0.1 and len(calls) == 1  # not even tried
    status.mark(True)
    assert not status.offline()


# ---------- hosted workspaces ----------

@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", hosted=True,
                        public_url=APP_URL)
    application = create_app(settings)
    with TestClient(application, base_url=APP_URL):
        yield application


def member(app, email: str = "asha@acme.in", plan: str = "community") -> tuple[TestClient, str]:
    state = app.state.stardeck
    ws = state.create_workspace("Acme", plan)
    token = Invites(state.db).create(ws.id, email)
    c = TestClient(app, base_url=APP_URL)
    assert c.post(f"/api/auth/invite/{token}", json={"password": PASSWORD}).status_code == 200
    return c, ws.id


def sample_source(c: TestClient) -> str:
    return next(s["id"] for s in c.get("/api/sources").json() if s["sample"])


def reply(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


SUGGESTION = {"columns": [{"table": "orders", "column": "sts", "description": "Order status", "confidence": "high"}]}


def test_daily_allowance_counts_and_resets_at_midnight_ist(app, monkeypatch):
    c, ws = member(app)
    state = app.state.stardeck
    state.workspaces.update(ws, limits_override={"ai_messages_per_day": 2})
    src = sample_source(c)
    clock = [datetime(2026, 10, 3, 23, 59, 0, tzinfo=IST)]
    monkeypatch.setattr(usage_mod, "now_ist", lambda: clock[0])
    with fake_ai(app, lambda request: reply(SUGGESTION)):
        assert c.post(f"/api/sources/{src}/suggest").status_code == 200
        assert c.post(f"/api/sources/{src}/suggest").status_code == 200
        third = c.post(f"/api/sources/{src}/suggest")
        assert third.status_code == 429 and "2 assistant messages" in third.json()["detail"]
        assert third.headers["retry-after"] == "60"  # a minute to midnight
        ai = c.get("/api/usage").json()["ai"]
        assert ai["assistant"] == {"used": 2, "limit": 2} and ai["resetsAt"] == "2026-10-04T00:00:00+05:30"
        clock[0] += timedelta(minutes=2)  # a new day in India
        assert c.post(f"/api/sources/{src}/suggest").status_code == 200
        assert c.get("/api/usage").json()["ai"]["assistant"]["used"] == 1


def test_failures_on_our_side_dont_count(app):
    c, _ = member(app)
    src = sample_source(c)
    res = c.post(f"/api/sources/{src}/suggest")  # the AI server is unreachable
    assert res.status_code == 503 and "AI" in res.json()["detail"]
    dash = next(d["id"] for d in c.get("/api/dashboards").json())
    assert c.post(f"/api/dashboards/{dash}/chat", json={"message": "hello"}).status_code == 503
    assert c.get("/api/usage").json()["ai"]["assistant"]["used"] == 0
    out = c.post("/api/dashboards/generate", json={"sourceIds": [src]})
    assert json.loads(out.text.strip().splitlines()[-1])["type"] == "error"
    assert c.get("/api/usage").json()["ai"]["generation"]["used"] == 0


def test_allowances_hold_under_concurrency(app):
    _, ws = member(app)
    state = app.state.stardeck
    state.workspaces.update(ws, limits_override={"ai_messages_per_day": 10})
    granted, refused = [], []

    def go() -> None:
        try:
            granted.append(state.quotas.charge(ws, "assistant"))
        except QuotaExceeded:
            refused.append(1)

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(granted) == 10 and len(refused) == 10
    assert state.quotas.used(ws, "assistant") == 10


def test_self_hosted_counts_nothing(client):
    state = client.app.state.stardeck
    assert state.quotas.charge("default", "assistant") is None
    assert client.get("/api/usage").json()["ai"]["assistant"]["limit"] is None


PLAN = {"reasoning": "r", "name": "Orders", "description": "d", "widgets": [
    {"title": f"Orders {i}", "request": "number of orders", "kind": "kpi"} for i in range(6)]}
DRAFT = {"reasoning": "r", "title": "t", "summary": "s.", "chart_type": "kpi", "sql": "SELECT COUNT(*) AS n FROM orders"}


def test_one_build_per_workspace_and_two_overall(app):
    planning, release = threading.Event(), threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["response_format"]["json_schema"]["name"] == "DashboardPlan":
            planning.set()
            release.wait(10)
            return reply(PLAN)
        return reply(DRAFT)

    a, _ = member(app, "a@acme.in", "enterprise")
    b, _ = member(app, "b@beta.in", "enterprise")
    c, _ = member(app, "c@gamma.in", "enterprise")
    with fake_ai(app, handler):
        results = {}
        first = threading.Thread(target=lambda: results.setdefault("a", a.post("/api/dashboards/generate",
                                                                               json={"sourceIds": [sample_source(a)]})))
        first.start()
        assert planning.wait(10)
        again = a.post("/api/dashboards/generate", json={"sourceIds": [sample_source(a)]})
        assert again.status_code == 429 and "already being built" in again.json()["detail"]
        second = threading.Thread(target=lambda: results.setdefault("b", b.post("/api/dashboards/generate",
                                                                                json={"sourceIds": [sample_source(b)]})))
        second.start()
        time.sleep(0.3)
        third = c.post("/api/dashboards/generate", json={"sourceIds": [sample_source(c)]})
        assert third.status_code == 429 and "other dashboards" in third.json()["detail"]
        release.set()
        first.join(10)
        second.join(10)
    for key in ("a", "b"):
        assert json.loads(results[key].text.strip().splitlines()[-1])["type"] == "done"
    assert a.get("/api/usage").json()["ai"]["generation"]["used"] == 1


@contextmanager
def serve(app):
    """The app on a real server (the test client buffers whole responses, so it can't hang up early)."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(10)


def test_closing_the_page_stops_the_build(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        calls.append(name)
        if name != "DashboardPlan":
            time.sleep(0.3)
        return reply(PLAN if name == "DashboardPlan" else DRAFT)

    app = create_app(Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1"))
    with serve(app) as url, fake_ai(app, handler), httpx.Client(base_url=url, timeout=10) as http:
        with http.stream("POST", "/api/dashboards/generate", json={"sourceIds": ["retail-demo"]}) as res:
            for line in res.iter_lines():
                if line and json.loads(line)["type"] == "plan":
                    break  # the person closes the page
        time.sleep(1.5)
        widgets = calls.count("WidgetDraft")
        assert widgets <= 3, calls  # the ones already running finish; the rest never start
        assert app.state.stardeck.generations.try_acquire("default") is None  # its slot was released
        app.state.stardeck.generations.release("default")
        assert [d["id"] for d in http.get("/api/dashboards").json()] == ["sales-overview"]  # nothing saved
        time.sleep(0.5)
        assert calls.count("WidgetDraft") == widgets  # and nothing more was asked of the AI


def test_quiet_builds_send_keepalives(app, monkeypatch):
    monkeypatch.setattr(api_dash, "PING_EVERY_S", 0.2)

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["response_format"]["json_schema"]["name"] == "DashboardPlan":
            time.sleep(1.2)
            return reply(PLAN)
        return reply(DRAFT)

    c, _ = member(app, plan="enterprise")
    with fake_ai(app, handler):
        out = c.post("/api/dashboards/generate", json={"sourceIds": [sample_source(c)]})
    kinds = [json.loads(line)["type"] for line in out.text.strip().splitlines()]
    assert kinds[0] == "ping" and "plan" in kinds and kinds[-1] == "done"


def test_label_shortening_quietly_stops_using_the_ai(app):
    c, ws = member(app)
    app.state.stardeck.workspaces.update(ws, limits_override={"ai_label_batches_per_day": 1})
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(1)
        return reply({"labels": [{"original": "Electronics and accessories", "short": "Electronics"}]})

    with fake_ai(app, handler):
        first = c.post("/api/labels/shorten", json={"labels": ["Electronics and accessories"], "maxChars": 12}).json()
        second = c.post("/api/labels/shorten", json={"labels": ["Home and kitchen appliances"], "maxChars": 12})
    assert first["ai"] is True and second.status_code == 200 and second.json()["ai"] is False
    assert len(asked) == 1
