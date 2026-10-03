"""Sliding-window rate limits, in memory (Stardeck runs as one process)."""

import ipaddress
import math
import threading
import time
from collections import deque

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _window(self, key: str, window_s: float, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        return hits

    def retry_after(self, key: str, limit: int, window_s: float) -> float:
        """Seconds until `key` may go again (0 = now), without counting this as a hit."""
        now = time.monotonic()
        with self._lock:
            hits = self._window(key, window_s, now)
            return 0.0 if len(hits) < limit else hits[0] + window_s - now

    def hit(self, key: str, limit: int, window_s: float) -> float:
        """Count a hit unless over the limit. Returns seconds to wait (0 = allowed and counted)."""
        now = time.monotonic()
        with self._lock:
            hits = self._window(key, window_s, now)
            if len(hits) >= limit:
                return hits[0] + window_s - now
            hits.append(now)
            if len(self._hits) > 50_000:  # forget idle keys so memory stays bounded
                self._hits = {k: v for k, v in self._hits.items() if v}
            return 0.0

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_key(request: Request) -> str:
    """The caller's address; IPv6 by /64, since one household or phone gets a whole /64."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(ip, ipaddress.IPv6Address) and not ip.ipv4_mapped:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip.ipv4_mapped or ip) if isinstance(ip, ipaddress.IPv6Address) else str(ip)


def too_many(wait_s: float, what: str = "attempts") -> HTTPException:
    minutes = max(1, math.ceil(wait_s / 60))
    return HTTPException(429, f"Too many {what}. Try again in {minutes} minute{'s' if minutes > 1 else ''}.",
                         headers={"Retry-After": str(math.ceil(wait_s))})
