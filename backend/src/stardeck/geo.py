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
_BUSY = object()


class Geocoder:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._lock = threading.Lock()
        self._last = 0.0
        contact = settings.site_url or settings.public_url
        agent = f"Stardeck/{__version__} (+{contact})" if settings.hosted and contact else \
            f"Stardeck/{__version__} (self-hosted dashboards)"
        # Hosted: every workspace shares one lookup per second, so each request does only a few.
        self.max_per_call = 5 if settings.hosted else MAX_LOOKUPS_PER_CALL
        self._http = httpx.Client(base_url=settings.geocoder_url.rstrip("/"), timeout=15, transport=transport,
                                  headers={"User-Agent": agent})

    @property
    def enabled(self) -> bool:
        return self.settings.geocoder != "off"

    def _lookup(self, place: str):
        """Coordinates, None (not found), or _BUSY when another request is looking something up:
        callers don't queue behind each other; the browser asks again for what's pending."""
        if not self._lock.acquire(blocking=False):
            return _BUSY
        try:  # one request at a time, at most one per second
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
        finally:
            self._lock.release()
        if not hits:
            return None
        return float(hits[0]["lat"]), float(hits[0]["lon"])

    def locate(self, db: Session, places: list[str], lookup: bool = True) -> tuple[dict[str, list[float] | None], int]:
        """Coordinates for each place (None if unknown), plus how many are still pending. Without
        `lookup`, only places already in the cache (public pages never query the geocoding service)."""
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
        done = 0
        for key in todo[:self.max_per_call] if self.enabled and lookup else []:
            coords = self._lookup(by_key[key][0])
            if coords is _BUSY:
                break
            db.merge(GeocodeRecord(query=key, lat=coords and coords[0], lon=coords and coords[1]))
            db.commit()
            found[key] = list(coords) if coords else None
            done += 1
        # Only places with an answer: the rest are pending, and the browser asks for them again.
        out = {name: found[key] for key, variants in by_key.items() if key in found for name in variants}
        return out, len(todo) - done if self.enabled else 0
