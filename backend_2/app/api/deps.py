from collections.abc import AsyncGenerator, Callable

from fastapi import Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, oauth2_scheme
from app.db.session import get_session
from app.models.user import User, UserRole
from neo4j import AsyncSession as AsyncNeo4jSession

from app.graph.neo4j import get_neo4j_session, get_optional_neo4j_session
from app.repositories.user_repository import UserRepository
from app.schemas.user import TokenPayload
from app.services.audit_service import AuditService
from app.services.game_service import GameService
from app.services.library_service import LibraryService
from app.services.note_service import NoteService
from app.services.notification_service import NotificationService
from app.services.media_service import MediaService
from app.services.ontology_instance_service import OntologyInstanceService
from app.services.ontology_service import OntologyService
from app.services.user_service import UserService


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


async def get_ontology_service(
    session: AsyncSession = Depends(get_db_session),
) -> OntologyService:
    return OntologyService(session)


async def get_user_service(
    session: AsyncSession = Depends(get_db_session),
) -> UserService:
    return UserService(session)


async def get_audit_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuditService:
    return AuditService(session)


async def get_notification_service(
    session: AsyncSession = Depends(get_db_session),
) -> NotificationService:
    return NotificationService(session)


def get_media_service() -> MediaService:
    return MediaService()


async def get_game_service(
    session: AsyncSession = Depends(get_db_session),
) -> GameService:
    return GameService(session)


async def get_library_service(
    session: AsyncSession = Depends(get_db_session),
) -> LibraryService:
    return LibraryService(session)


async def get_note_service(
    session: AsyncSession = Depends(get_db_session),
    notification_service: NotificationService = Depends(get_notification_service),
) -> NoteService:
    return NoteService(session, notification_service)


async def get_ontology_instance_service(
    sql_session: AsyncSession = Depends(get_db_session),
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> OntologyInstanceService:
    return OntologyInstanceService(sql_session, graph_session)


async def get_optional_ontology_instance_service(
    sql_session: AsyncSession = Depends(get_db_session),
    graph_session: AsyncNeo4jSession | None = Depends(get_optional_neo4j_session),
) -> OntologyInstanceService | None:
    if graph_session is None:
        return None
    return OntologyInstanceService(sql_session, graph_session)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    unauthorized_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = TokenPayload.model_validate(decode_access_token(token))
    except (ValueError, ValidationError) as exc:  # pragma: no cover - invalid token
        raise unauthorized_exception from exc

    if payload.sub is None:
        raise unauthorized_exception

    repository = UserRepository(session)
    user = await repository.get(int(payload.sub))
    if user is None:
        raise unauthorized_exception
    return user


def require_roles(*roles: UserRole) -> Callable[..., User]:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if roles and current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency to require admin role."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def get_current_active_admin_or_world_builder(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency to require admin or world_builder role."""
    if current_user.role not in (UserRole.ADMIN, UserRole.WORLD_BUILDER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or world builder privileges required",
        )
    return current_user
