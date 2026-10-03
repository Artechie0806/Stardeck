"""Is the AI server up? Checked at most every 20 s, one check at a time, with short timeouts.

Every page load asks. Without the cache, an AI server that's switched off would hold one request
per page load for the whole connect timeout; with it, the answer is instant.
"""

import threading
import time

import httpx

from stardeck.config import Settings
from stardeck.llm.client import LLMClient, LLMError

TTL_S = 20.0
PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)


class LLMStatus:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._lock = threading.Lock()
        self._value: dict | None = None
        self._at = 0.0

    def mark(self, reachable: bool) -> None:
        """What a real AI call just saw, so fast-failing and the status follow reality."""
        before = self._value or {}
        if reachable:
            self._value = {"reachable": True, "model_available": before.get("model_available", True)}
        else:
            self._value = {"reachable": False, "error": "the AI server can't be reached right now"}
        self._at = time.monotonic()

    def offline(self) -> bool:
        """Known to be offline in the last few seconds: fail at once instead of trying again."""
        value = self._value
        return value is not None and not value.get("reachable") and time.monotonic() - self._at < TTL_S

    def get(self) -> dict:
        """{"reachable": bool, "model_available"?: bool, "error"?: str}"""
        if self._value is not None and time.monotonic() - self._at < TTL_S:
            return self._value
        # Someone else is already checking: answer with the last result rather than wait.
        if not self._lock.acquire(blocking=self._value is None):
            return self._value or {"reachable": False}
        try:
            if self._value is not None and time.monotonic() - self._at < TTL_S:
                return self._value
            client = LLMClient(self.settings, transport=self._transport, timeout=PROBE_TIMEOUT)
            try:
                value = {"reachable": True, "model_available": self.settings.llm_model in client.list_models()}
            except LLMError as exc:
                value = {"reachable": False, "error": str(exc)}
            finally:
                client.close()
            self._value, self._at = value, time.monotonic()
            return value
        finally:
            self._lock.release()
