import httpx

from stardeck.charts import derive_roles, finalize
from stardeck.config import Settings
from stardeck.geo import Geocoder
from stardeck.query import QueryResult


def test_map_from_place_names_and_coordinates():
    by_city = derive_roles(QueryResult(["city", "revenue"], [["Pune", 5], ["Delhi", 7]]))
    assert finalize("map", "none", by_city)[0] == {"type": "map", "location": "city", "y": "revenue", "label": "city"}
    pts = derive_roles(QueryResult(["store", "latitude", "longitude", "sales"], [["A", 18.5, 73.8, 9]]), y="sales")
    spec, _ = finalize("map", "none", pts)
    assert spec["lat"] == "latitude" and spec["lon"] == "longitude" and spec["y"] == "sales"
    no_place = derive_roles(QueryResult(["category", "n"], [["A", 1], ["B", 2]]))
    spec, notes = finalize("map", "none", no_place)
    assert spec["type"] != "map" and notes


def test_geocoder_caches_and_throttles(client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["q"])
        body = [] if request.url.params["q"] == "Atlantis" else [{"lat": "18.5", "lon": "73.8"}]
        return httpx.Response(200, json=body)

    state = client.app.state.stardeck
    state.geocoder = Geocoder(Settings(), transport=httpx.MockTransport(handler))
    res = client.post("/api/geocode", json={"places": ["Pune", "Atlantis", "pune "]}).json()
    assert res == {"places": {"Pune": [18.5, 73.8], "pune": [18.5, 73.8], "Atlantis": None}, "pending": 0}
    assert calls == ["Pune", "Atlantis"]  # "pune " shares the "Pune" lookup
    client.post("/api/geocode", json={"places": ["Pune", "Atlantis"]})
    assert calls == ["Pune", "Atlantis"]  # second call is fully cached
    assert client.get("/api/config").json()["geocoding"] is True
