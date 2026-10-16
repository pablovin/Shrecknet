from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.api.deps import get_current_user
from app.main import app
from app.models import User
from app.services.users import user_service


@pytest.mark.asyncio
async def test_user_summaries_are_bounded_authenticated_and_public(
    client, session_maker
):
    assert (await client.get("/contracts/users?ids=1")).status_code == 401
    async with session_maker() as db:
        db.add_all(
            [
                User(
                    id=1,
                    username="one",
                    full_name="One",
                    email="private@example.test",
                    hashed_password="secret",
                    avatar_url="/media/one.png",
                ),
                User(
                    id=2,
                    username="two",
                    full_name="Two",
                    email="two@example.test",
                    hashed_password="secret",
                ),
            ]
        )
        await db.commit()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="player"
    )
    try:
        result = await client.get("/contracts/users?ids=2&ids=1&ids=1&ids=999")
        assert result.status_code == 200, result.text
        assert result.json() == [
            {
                "id": 1,
                "username": "one",
                "full_name": "One",
                "avatar_url": "/media/one.png",
            },
            {"id": 2, "username": "two", "full_name": "Two", "avatar_url": None},
        ]
        for query in [
            "",
            "?ids=0",
            "?ids=-1",
            "?ids=bad",
            "?" + "&".join("ids=1" for _ in range(101)),
        ]:
            assert (await client.get("/contracts/users" + query)).status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_user_summary_batch_uses_one_read(session_maker, test_engine):
    counts = []

    def capture(*args):
        counts.append(1)

    async with session_maker() as db:
        db.add(
            User(
                id=1,
                username="one",
                full_name="One",
                email="one@example.test",
                hashed_password="secret",
            )
        )
        await db.commit()
        event.listen(test_engine.sync_engine, "before_cursor_execute", capture)
        try:
            result = await user_service.summaries_async(db, list(range(1, 101)))
            assert [user.id for user in result] == [1]
            assert len(counts) == 1
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", capture)
