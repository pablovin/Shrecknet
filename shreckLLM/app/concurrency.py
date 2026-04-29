from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from app.errors import ProviderOverloadedError


class RequestLimiter:
    def __init__(self, *, max_concurrent: int) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._guard = asyncio.Lock()
        self._in_flight = 0
        self._waiting = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self, *, wait_timeout_s: float):
        async with self._guard:
            self._waiting += 1
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=max(0.01, wait_timeout_s))
        except asyncio.TimeoutError as exc:
            raise ProviderOverloadedError("request queue wait timeout exceeded") from exc
        finally:
            async with self._guard:
                self._waiting = max(0, self._waiting - 1)

        async with self._guard:
            self._in_flight += 1
        try:
            yield
        finally:
            self._sem.release()
            async with self._guard:
                self._in_flight = max(0, self._in_flight - 1)
