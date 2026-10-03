from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from stardeck.config import Settings
from stardeck.llm import LLMClient
from stardeck.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", seed_demo=True)


@pytest.fixture
def client(settings) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def demo_db(client, settings):
    """Read-only connection to the seeded demo DB, so tests compute expected numbers instead of hardcoding."""
    import sqlite3

    db = sqlite3.connect(f"file:{(settings.data_dir / 'demo' / 'retail.sqlite').resolve()}?mode=ro", uri=True)
    yield db
    db.close()


def completed_orders(db, year: int) -> int:
    return db.execute("SELECT COUNT(*) FROM orders WHERE sts = 1 AND order_date LIKE ?", (f"{year}%",)).fetchone()[0]


@contextmanager
def fake_ai(app, handler):
    """Answer every AI call (chat, suggestions, labels, dashboard builds) with `handler`, through the
    app's real AI queue."""
    state = app.state.stardeck
    original = state.llm

    def make(background: bool = False, cancel=None, wait_s=None) -> LLMClient:
        return LLMClient(Settings(_env_file=None), transport=httpx.MockTransport(handler), gate=state.gate,
                         background=background, cancel=cancel, wait_s=wait_s)

    state.llm = make
    try:
        yield
    finally:
        state.llm = original
