from __future__ import annotations

import asyncio


class ConversationLockManager:
    """In-process per-conversation locking for ordering consistency."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get_lock(self, conversation_id: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[conversation_id] = lock
            return lock
