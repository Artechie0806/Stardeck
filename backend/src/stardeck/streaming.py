"""Long work streamed to the browser as NDJSON, stopped when the browser goes away.

A sync generator behind a StreamingResponse isn't closed when the client disconnects (only when
it's garbage collected), so its work kept going for nobody. Here the work runs in its own thread
and reports through a queue; the response relays it from an async generator, which the server
cancels right away on disconnect, and its `finally` sets the flag the work checks.
"""

import json
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable

import anyio

Emit = Callable[[dict], None]
_DONE = object()


def relay(work: Callable[[Emit, threading.Event], None], *, name: str,
          ping_every_s: float | None = None) -> AsyncIterator[str]:
    """Start `work(emit, cancelled)` in a thread; return its events as NDJSON lines. With
    `ping_every_s`, a {"type": "ping"} line goes out whenever it's been quiet that long, so proxies
    don't close a stream that's still working."""
    events: queue.Queue = queue.Queue()
    cancel = threading.Event()

    def run() -> None:
        try:
            work(events.put, cancel)
        finally:
            events.put(_DONE)

    threading.Thread(target=run, name=name, daemon=True).start()

    async def lines() -> AsyncIterator[str]:
        quiet_since = time.monotonic()
        try:
            while True:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    if ping_every_s and time.monotonic() - quiet_since >= ping_every_s:
                        quiet_since = time.monotonic()
                        yield json.dumps({"type": "ping"}) + "\n"
                    await anyio.sleep(0.05)
                    continue
                if event is _DONE:
                    return
                quiet_since = time.monotonic()
                yield json.dumps(event) + "\n"
        finally:
            cancel.set()  # finished, or the browser went away: the work stops at its next check

    return lines()
