"""
Bridge APScheduler (sync, background thread) to FastAPI's asyncio loop.

Motor binds to the event loop used for the first I/O. `asyncio.run()` creates and then
closes a loop after each call, so repeated cron ticks hit RuntimeError: Event loop is closed.
Running cron coroutines on the app's long-lived loop fixes that.
"""

from __future__ import annotations

import asyncio
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")

_app_loop: asyncio.AbstractEventLoop | None = None


def register_app_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once from FastAPI startup with asyncio.get_running_loop()."""
    global _app_loop
    _app_loop = loop


def run_coroutine_on_app_loop(
    coro: Coroutine[Any, Any, T],
    *,
    timeout: float | None = 300,
) -> T:
    """
    Run `coro` on the registered app loop (thread-safe). Use from APScheduler job callbacks.

    ``timeout=None`` waits until the coroutine finishes (needed for long sequential scrapes).
    """
    loop = _app_loop
    if loop is None or not loop.is_running():
        raise RuntimeError(
            "App event loop is not registered or not running. "
            "Call register_app_event_loop(asyncio.get_running_loop()) from FastAPI startup "
            "before starting APScheduler."
        )
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)
