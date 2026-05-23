import pytest

from sqlalchemy import select

from app.core import config_store
from app.core.config_store import (
    LLMModelTarget,
    get_settings,
    reload_settings,
    update_settings,
)
from app.api.routers.graphrag import ensure_index
from app.graphrag import embedding_service
from app.db import jobs_session, session as db_session
from app.db.init_db import init_db
from app.db.session import get_sessionmaker
from app.models import User, UserRole, World
from app.core.security import get_password_hash
from app.services.user_service import UserService


def _reset_runtime_state() -> None:
    config_store._settings_cache = None
    db_session._engine = None
    db_session._sessionmaker = None
    db_session._engine_key = None
    jobs_session._jobs_engine = None
    jobs_session._jobs_sessionmaker = None
    jobs_session._jobs_engine_key = None


def test_empty_init_has_no_demo_seed(monkeypatch, tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'shrecknet.db'}"
    jobs_database_url = f"sqlite:///{tmp_path / 'shrecknet_jobs.db'}"
    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHRECKNET_DATABASE_URL", database_url)
    monkeypatch.setenv("SHRECKNET_JOBS_DATABASE_URL", jobs_database_url)
    _reset_runtime_state()

    init_db()

    settings = get_settings()
    assert settings.database_url == database_url

    sessionmaker = get_sessionmaker()
    with sessionmaker() as session:
        users = session.execute(select(User)).scalars().all()
        worlds = session.execute(select(World)).scalars().all()

    assert users == []
    assert worlds == []


@pytest.mark.asyncio
async def test_first_registered_user_becomes_admin() -> None:
    class FakeSession:
        async def commit(self) -> None:
            return None

        async def refresh(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("execute should not be called in this scenario")

        async def flush(self) -> None:
            return None

    class FakeRepository:
        def __init__(self) -> None:
            self._users: list[User] = []

        async def has_any(self) -> bool:
            return bool(self._users)

        async def get_by_username(self, username: str) -> User | None:
            return next((user for user in self._users if user.username == username), None)

        async def get_by_email(self, email: str) -> User | None:
            return next((user for user in self._users if user.email == email), None)

        async def create(self, data: dict) -> User:
            payload = {"role": UserRole.PLAYER, **data}
            user = User(id=len(self._users) + 1, **payload)
            self._users.append(user)
            return user

    service = UserService(FakeSession())
    service.repository = FakeRepository()

    first = await service.register_user(
        {
            "username": "admin",
            "email": "admin@example.com",
            "full_name": "Admin User",
            "password": "secret",
        }
    )
    second = await service.register_user(
        {
            "username": "player",
            "email": "player@example.com",
            "full_name": "Player User",
            "password": "secret",
        }
    )

    assert first.role == UserRole.ADMIN
    assert second.role == UserRole.PLAYER


@pytest.mark.asyncio
async def test_authenticate_user_accepts_username_and_email() -> None:
    class FakeSession:
        async def commit(self) -> None:
            return None

        async def refresh(self, *_args, **_kwargs) -> None:
            return None

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("execute should not be called in this scenario")

        async def flush(self) -> None:
            return None

    user = User(
        id=1,
        username="player1",
        email="player1@example.com",
        full_name="Player One",
        hashed_password=get_password_hash("secret"),
        password="",
        role=UserRole.PLAYER,
    )

    class FakeRepository:
        async def get_by_username(self, username: str) -> User | None:
            return user if username == user.username else None

        async def get_by_email(self, email: str) -> User | None:
            return user if email == user.email else None

    service = UserService(FakeSession())
    service.repository = FakeRepository()

    assert await service.authenticate_user("player1", "secret") == user
    assert await service.authenticate_user("player1@example.com", "secret") == user


def test_config_store_overrides_non_bootstrap_env_but_not_bootstrap_env(
    monkeypatch, tmp_path
) -> None:
    env_db_url = f"sqlite:///{tmp_path / 'env.db'}"
    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHRECKNET_DATABASE_URL", env_db_url)
    _reset_runtime_state()

    initial = get_settings()
    assert initial.model_novelist == LLMModelTarget(provider="openai", name="gpt-5-nano")
    assert initial.database_url == env_db_url

    updated = update_settings(
        {
            "model_novelist": {"provider": "openai", "name": "db-model"},
            "database_url": f"sqlite:///{tmp_path / 'db-value.db'}",
        }
    )
    assert updated.model_novelist == LLMModelTarget(provider="openai", name="db-model")
    assert updated.database_url == env_db_url

    reloaded = reload_settings()
    assert reloaded.model_novelist == LLMModelTarget(provider="openai", name="db-model")
    assert reloaded.database_url == env_db_url


def test_config_store_rejects_legacy_string_model_updates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path))
    _reset_runtime_state()

    with pytest.raises(ValueError, match="must be an object with provider/name"):
        update_settings({"model_elder": "legacy-elder-model"})


@pytest.mark.asyncio
async def test_graphrag_index_status_uses_config_store_settings(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path))
    _reset_runtime_state()

    update_settings(
        {
            "embedding_model_id": "test-model-id",
            "embedding_dimension": 123,
        }
    )

    async def _fake_ensure_vector_index(self, index_name: str) -> bool:
        assert index_name == "entity_text_vec_idx"
        return True

    monkeypatch.setattr(
        embedding_service.EmbeddingService,
        "ensure_vector_index",
        _fake_ensure_vector_index,
    )

    response = await ensure_index(graph_session=None, current_user=None)

    assert response.embedding_model == "test-model-id"
    assert response.embedding_dim == 123
