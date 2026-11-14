"""Shared asynchronous streaming helpers.

The helpers in this module are deliberately provider-agnostic. Providers own
their SDK-specific error mapping, while this module owns two cross-cutting
concerns:

* enforcing an idle timeout and an overall deadline on an async iterator; and
* closing the underlying stream exactly once, regardless of how iteration
  ends (completion, error, timeout, or cancellation).

Closing is best effort. A provider stream may expose either an asynchronous
``aclose`` or a synchronous ``close``; both shapes are supported because the
Groq and Ollama SDKs differ.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from typing import Any


logger = logging.getLogger(__name__)


async def close_async_stream(stream: Any) -> None:
    """Close an async iterator without masking the original failure."""

    if stream is None:
        return

    for attribute in ("aclose", "close"):
        closer = getattr(stream, attribute, None)

        if not callable(closer):
            continue

        try:
            result = closer()

            if inspect.isawaitable(result):
                await result

        except Exception:
            # Cleanup must never replace the exception that ended iteration.
            logger.debug(
                "Failed to close an async stream via %s.",
                attribute,
                exc_info=True,
            )

        return


async def iterate_with_timeouts(
    source: Any,
    *,
    idle_timeout: float | None = None,
    overall_timeout: float | None = None,
) -> AsyncIterator[Any]:
    """Yield items from ``source`` with bounded waiting.

    ``idle_timeout`` bounds the wait for one item. ``overall_timeout`` bounds
    the total lifetime of the iteration. A non-positive value disables the
    corresponding limit. The source is always closed in ``finally``.
    """

    iterator = source.__aiter__()
    idle = float(idle_timeout or 0)
    overall = float(overall_timeout or 0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + overall if overall > 0 else None

    try:
        while True:
            timeout = idle

            if deadline is not None:
                remaining = deadline - loop.time()

                if remaining <= 0:
                    raise asyncio.TimeoutError

                timeout = min(timeout, remaining) if timeout > 0 else remaining

            try:
                if timeout > 0:
                    item = await asyncio.wait_for(
                        iterator.__anext__(),
                        timeout=timeout,
                    )
                else:
                    item = await iterator.__anext__()

            except StopAsyncIteration:
                break

            yield item

    finally:
        await close_async_stream(source)


__all__ = ["close_async_stream", "iterate_with_timeouts"]
