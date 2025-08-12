import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect, text
from app.database import _migrate_sessionpolloption


@pytest.mark.anyio
async def test_sessionpolloption_timezone_migration():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE sessionpolloption (id INTEGER PRIMARY KEY AUTOINCREMENT, poll_id INTEGER NOT NULL, proposed_time DATETIME NOT NULL)"
        )
        await conn.run_sync(lambda c: _migrate_sessionpolloption(c, inspect(c), text))
        columns = await conn.run_sync(
            lambda c: [
                col["name"] for col in inspect(c).get_columns("sessionpolloption")
            ]
        )
        assert "timezone" in columns
    await engine.dispose()
