"""Helpers for cancellation-safe work delegated to Python worker threads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


async def run_blocking_to_completion(
    function: Callable[..., T], *args: Any, **kwargs: Any
) -> T:
    """Keep a worker thread owned until it finishes, even if its caller cancels.

    ``asyncio.to_thread`` cannot stop native inference already running in a
    worker. Shielding and draining it prevents a cancelled request from
    releasing model/semaphore ownership while Piper or Whisper is still using
    shared model state. The caller still receives cancellation immediately
    after the worker has safely exited.
    """

    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(worker)
        except Exception:
            # The cancellation takes precedence over an inference failure from
            # work the cancelled request no longer needs.
            pass
        raise
