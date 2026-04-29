from __future__ import annotations

import pytest

from app.locking import ConversationLockManager


@pytest.mark.asyncio
async def test_lock_manager_returns_same_lock_per_conversation() -> None:
    manager = ConversationLockManager()
    lock1 = await manager.get_lock("conv")
    lock2 = await manager.get_lock("conv")
    lock3 = await manager.get_lock("other")

    assert lock1 is lock2
    assert lock1 is not lock3
