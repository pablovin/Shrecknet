from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.config_store import UserCreationMode
from app.db.migrations import migrate_user_approval_columns
from app.models.user import User, UserApprovalStatus, UserRole
from app.services import user_service as user_service_module
from app.services.user_service import UserCreationStoppedError, UserService


class FakeSession:
    async def commit(self) -> None:
        return None

    async def refresh(self, *_args, **_kwargs) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("entity assignment is not expected")

    async def flush(self) -> None:
        return None


class FakeRepository:
    def __init__(self) -> None:
        self.users: list[User] = []

    async def has_any(self) -> bool:
        return bool(self.users)

    async def get_by_username(self, username: str) -> User | None:
        return next((user for user in self.users if user.username == username), None)

    async def get_by_email(self, email: str) -> User | None:
        return next((user for user in self.users if user.email == email), None)

    async def create(self, data: dict) -> User:
        user = User(id=len(self.users) + 1, password="", **data)
        self.users.append(user)
        return user

    async def update(self, user: User, data: dict) -> User:
        for key, value in data.items():
            setattr(user, key, value)
        return user

    async def list_by_approval_status(self, status: UserApprovalStatus) -> list[User]:
        return [user for user in self.users if user.approval_status == status]


def _service() -> tuple[UserService, FakeRepository]:
    service = UserService(FakeSession())
    repository = FakeRepository()
    service.repository = repository
    return service, repository


def _payload(name: str, *, role: UserRole = UserRole.ADMIN) -> dict:
    return {
        "username": name,
        "email": f"{name}@example.com",
        "full_name": name,
        "password": "secret",
        "role": role,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        (UserCreationMode.MODERATED, UserApprovalStatus.PENDING),
        (UserCreationMode.ALLOWED, UserApprovalStatus.APPROVED),
    ],
)
async def test_registration_applies_mode_and_never_honors_elevated_role(
    monkeypatch, mode, expected_status
) -> None:
    monkeypatch.setattr(
        user_service_module,
        "get_settings",
        lambda: SimpleNamespace(user_creation_mode=mode),
    )
    service, _ = _service()

    first = await service.register_user(_payload("bootstrap"))
    second = await service.register_user(_payload("later", role=UserRole.ADMIN))

    assert first.role == UserRole.ADMIN
    assert first.approval_status == UserApprovalStatus.APPROVED
    assert second.role == UserRole.PLAYER
    assert second.approval_status == expected_status


@pytest.mark.asyncio
async def test_stopped_mode_blocks_later_registration_but_not_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(
        user_service_module,
        "get_settings",
        lambda: SimpleNamespace(user_creation_mode=UserCreationMode.STOPPED),
    )
    service, _ = _service()

    first = await service.register_user(_payload("bootstrap"))
    assert first.role == UserRole.ADMIN
    with pytest.raises(UserCreationStoppedError):
        await service.register_user(_payload("later"))


@pytest.mark.asyncio
async def test_pending_and_rejected_users_cannot_authenticate(monkeypatch) -> None:
    monkeypatch.setattr(
        user_service_module,
        "get_settings",
        lambda: SimpleNamespace(user_creation_mode=UserCreationMode.MODERATED),
    )
    service, repository = _service()
    admin = await service.register_user(_payload("bootstrap"))
    pending = await service.register_user(_payload("pending"))

    assert await service.authenticate_user("pending", "secret") is None
    approved = await service.decide_registration(pending, approved=True, actor=admin)
    assert await service.authenticate_user("pending", "secret") == approved
    # A second decision is prohibited and the rejected state also cannot log in.
    with pytest.raises(ValueError):
        await service.decide_registration(approved, approved=False, actor=admin)
    pending.approval_status = UserApprovalStatus.REJECTED
    assert await service.authenticate_user("pending", "secret") is None
    assert repository.users[1].approval_decided_by_user_id == admin.id


def test_user_approval_migration_marks_existing_users_approved(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(150), "
                "email VARCHAR(255))"
            )
        )
        connection.execute(
            text("INSERT INTO users (id, username, email) VALUES (1, 'old', 'old@example.com')")
        )
        migrate_user_approval_columns(connection)
        migrate_user_approval_columns(connection)
        row = connection.execute(
            text("SELECT approval_status, approval_decided_by_user_id, approval_decided_at FROM users")
        ).one()

    assert row == ("approved", None, None)
    assert {column["name"] for column in inspect(engine).get_columns("users")} >= {
        "approval_status",
        "approval_decided_by_user_id",
        "approval_decided_at",
    }
