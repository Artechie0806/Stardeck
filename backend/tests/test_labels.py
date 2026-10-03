import json

import httpx

from stardeck.config import Settings
from stardeck.deps import get_llm
from stardeck.labels import trim
from stardeck.llm import LLMClient


def test_trim():
    assert trim("Short", 10) == "Short"
    assert trim("Electronics & Home Appliances", 14) == "Electronics…"  # no dangling " &"
    assert len(trim("A very long product category name", 12)) <= 12


def test_shorten_validates_and_caches(client):
    calls = []
    reply = {"labels": [
        {"original": "Electronics & Home Appliances", "short": "Electronics & Home"},  # 18 chars: too long
        {"original": "International Wholesale Customers", "short": "Intl Wholesale"},
        {"original": "International Wholesale Partners", "short": "Intl Wholesale"},   # duplicate
    ]}

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    client.app.dependency_overrides[get_llm] = lambda: LLMClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        labels = ["Pune", "Electronics & Home Appliances", "International Wholesale Customers",
                  "International Wholesale Partners"]
        out = client.post("/api/labels/shorten", json={"labels": labels, "maxChars": 14}).json()
        again = client.post("/api/labels/shorten", json={"labels": labels, "maxChars": 14}).json()
    finally:
        client.app.dependency_overrides.clear()
    m = out["labels"]
    assert m["Pune"] == "Pune" and m["International Wholesale Customers"] == "Intl Wholesale"
    assert all(len(v) <= 14 for v in m.values()) and len(set(m.values())) == 4  # all fit, all distinct
    # Two suggestions failed the checks: one retry, then trimmed. Everything is cached once the model answered.
    assert len(calls) == 2 and again["labels"] == m


def test_shorten_without_model_trims(client):
    out = client.post("/api/labels/shorten", json={"labels": ["A very long product category name"], "maxChars": 12}).json()
    assert out["ai"] is False and len(out["labels"]["A very long product category name"]) <= 12


def test_compact():
    from stardeck.labels import compact
    assert compact("Apparel, Footwear and Accessories", 18) == "Apparel Footwear"
    assert compact("Apparel/Footwear/Access", 18) == "Apparel Footwear"
    assert compact("International Wholesale", 18) == "Inte. Wholesale"
    assert len(compact("Consumer Electronics & Smart Devices", 18)) <= 18
    assert compact("Home and Garden", 18) == "Home Garden"
