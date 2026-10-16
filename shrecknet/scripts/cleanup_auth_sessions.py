"""Run from shrecknet/: PYTHONPATH=. python scripts/cleanup_auth_sessions.py."""
import asyncio
import time

from app.db.session import get_db_session
from app.repositories.auth_sessions import AuthSessionRepository


async def cleanup() -> None:
    async for db in get_db_session():
        await AuthSessionRepository(db).cleanup(int(time.time()))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(cleanup())
