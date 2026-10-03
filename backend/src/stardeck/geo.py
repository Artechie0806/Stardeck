"""Place name -> coordinates, via Nominatim (OpenStreetMap), cached in Stardeck's database.

Nominatim's usage policy allows about one request per second with an identifying User-Agent,
so lookups are throttled and every answer (including "not found") is cached forever.
"""

import threading
import time

import httpx
from sqlalchemy.orm import Session

from stardeck import __version__
from stardeck.config import Settings
from stardeck.db import GeocodeRecord

MAX_LOOKUPS_PER_CALL = 25  # keeps a request under ~30 s; the client asks again for the rest


class Geocoder:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._lock = threading.Lock()
        self._last = 0.0
        self._http = httpx.Client(
            base_url=settings.geocoder_url.rstrip("/"), timeout=15, transport=transport,
            headers={"User-Agent": f"Stardeck/{__version__} (self-hosted dashboards)"},
        )

    @property
    def enabled(self) -> bool:
        return self.settings.geocoder != "off"

    def _lookup(self, place: str) -> tuple[float, float] | None:
        with self._lock:  # one request at a time, at most one per second
            wait = self._last + 1.05 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self._http.get("/search", params={"q": place, "format": "jsonv2", "limit": 1})
                resp.raise_for_status()
                hits = resp.json()
            except (httpx.HTTPError, ValueError):
                return None
            finally:
                self._last = time.monotonic()
        if not hits:
            return None
        return float(hits[0]["lat"]), float(hits[0]["lon"])

    def locate(self, db: Session, places: list[str]) -> tuple[dict[str, list[float] | None], int]:
        """Coordinates for each place (None if unknown), plus how many are still pending."""
        names = [p.strip() for p in places if p and p.strip()]
        by_key: dict[str, list[str]] = {}  # case-insensitive: "Pune" and "pune" share one lookup
        for name in names:
            by_key.setdefault(name.lower()[:300], []).append(name)
        found: dict[str, list[float] | None] = {}
        todo: list[str] = []
        for key in by_key:
            if hit := db.get(GeocodeRecord, key):
                found[key] = [hit.lat, hit.lon] if hit.lat is not None else None
            else:
                todo.append(key)
        lookups = todo[:MAX_LOOKUPS_PER_CALL] if self.enabled else []
        for key in lookups:
            coords = self._lookup(by_key[key][0])
            db.merge(GeocodeRecord(query=key, lat=coords and coords[0], lon=coords and coords[1]))
            db.commit()
            found[key] = list(coords) if coords else None
        out = {name: found.get(key) for key, variants in by_key.items() for name in variants}
        return out, len(todo) - len(lookups) if self.enabled else 0
