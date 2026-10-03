"""One queue in front of the AI server, shared by every request and workspace.

The AI server (one GPU) can only work on a few requests at once. Without a gate, every request
goes straight to it and waits there, eating into its timeout, until requests pile up and fail.
The gate allows `slots` at once (set STARDECK_LLM_PARALLEL to what the server runs in parallel),
lets a bounded number wait briefly, and turns the rest away with "busy" instead of piling up.

Background work (building dashboards, AI suggestions, label shortening) never takes the last
slot, so a chat question gets through even while a dashboard is being built.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from stardeck.llm.client import LLMBusy, LLMCancelled

INTERACTIVE_WAIT_S = 45.0
BACKGROUND_WAIT_S = 120.0


class Gate:
    def __init__(self, slots: int, max_waiting: int = 8):
        self.slots = max(1, slots)
        self.background_slots = max(1, self.slots - 1)
        self.max_waiting = max_waiting
        self._cond = threading.Condition()
        self._active = 0
        self._active_background = 0
        self._waiting = 0

    def _free(self, background: bool) -> bool:
        if self._active >= self.slots:
            return False
        return not (background and self._active_background >= self.background_slots)

    @contextmanager
    def slot(self, background: bool = False, wait_s: float | None = None,
             cancel: threading.Event | None = None) -> Iterator[None]:
        wait_s = (BACKGROUND_WAIT_S if background else INTERACTIVE_WAIT_S) if wait_s is None else wait_s
        deadline = time.monotonic() + wait_s
        with self._cond:
            if not self._free(background):
                if self._waiting >= self.max_waiting:
                    raise LLMBusy
                self._waiting += 1
                try:
                    while not self._free(background):
                        if cancel is not None and cancel.is_set():
                            raise LLMCancelled
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise LLMBusy
                        self._cond.wait(min(0.5, left))  # wakes on release, or to check cancel/deadline
                finally:
                    self._waiting -= 1
            self._active += 1
            if background:
                self._active_background += 1
        try:
            yield
        finally:
            with self._cond:
                self._active -= 1
                if background:
                    self._active_background -= 1
                self._cond.notify_all()

    @property
    def stats(self) -> dict[str, int]:
        with self._cond:
            return {"active": self._active, "background": self._active_background, "waiting": self._waiting,
                    "slots": self.slots}


class GenerationSlots:
    """Building a dashboard is long and heavy: at most `total` at once, one per workspace."""

    def __init__(self, total: int = 2):
        self.total = total
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def try_acquire(self, workspace_id: str) -> str | None:
        """None when it's this workspace's turn, else why not."""
        with self._lock:
            if workspace_id in self._busy:
                return "A dashboard is already being built in your workspace. Try again when it's done."
            if len(self._busy) >= self.total:
                return "Stardeck is building other dashboards right now. Try again in a minute."
            self._busy.add(workspace_id)
            return None

    def release(self, workspace_id: str) -> None:
        with self._lock:
            self._busy.discard(workspace_id)
