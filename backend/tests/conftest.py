import pytest
from fastapi.testclient import TestClient

from stardeck.config import Settings
from stardeck.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", llm_base_url="http://127.0.0.1:9/v1", seed_demo=True)


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
