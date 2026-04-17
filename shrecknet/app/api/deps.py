from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.exc import DatabaseError, OperationalError

from app.core.roles import get_minimum_role, has_role
from app.core.security import TokenError, decode_access_token
from app.db.session import AsyncSessionCompat, get_db_session
from app.models import User
from app.models.user import UserRole
from app.services.architect_service import ArchitectService
from app.services.audit_service import AuditService
from app.services.favorite_ontology_instance_service import FavoriteOntologyInstanceService
from app.services.media_service import MediaService
from app.services.ontology_instance_service import OntologyInstanceService
from app.services.ontology_service import OntologyService
from app.services.user_service import UserService
from app.services.users import user_service
from neo4j import AsyncSession as AsyncNeo4jSession
from app.graph.neo4j import get_neo4j_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSessionCompat = Depends(get_db_session),
) -> User:
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    sub = str(payload.get("sub") or "")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing subject")

    try:
        user = await user_service.get_async(session, sub)
    except (OperationalError, DatabaseError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable",
        ) from exc
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_roles(*roles: str):
    parsed = []
    for r in roles:
        raw = r.value if hasattr(r, "value") else r
        parsed.append(UserRole(str(raw).lower()))
    min_role = get_minimum_role(*parsed)

    def _inner(current_user: User = Depends(get_current_user)) -> User:
        role_value = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
        user_role = UserRole(role_value.lower())
        if min_role is None or not has_role(user_role, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _inner


async def get_ontology_service(
    session: AsyncSessionCompat = Depends(get_db_session),
) -> OntologyService:
    return OntologyService(session)


async def get_user_service(
    session: AsyncSessionCompat = Depends(get_db_session),
) -> UserService:
    return UserService(session)


async def get_ontology_instance_service(
    sql_session: AsyncSessionCompat = Depends(get_db_session),
    graph_session: AsyncNeo4jSession = Depends(get_neo4j_session),
) -> OntologyInstanceService:
    return OntologyInstanceService(sql_session, graph_session)


async def get_favorite_ontology_instance_service(
    session: AsyncSessionCompat = Depends(get_db_session),
) -> FavoriteOntologyInstanceService:
    return FavoriteOntologyInstanceService(session)


async def get_architect_service(
    session: AsyncSessionCompat = Depends(get_db_session),
) -> ArchitectService:
    return ArchitectService(session)


async def get_audit_service(
    session: AsyncSessionCompat = Depends(get_db_session),
) -> AuditService:
    return AuditService(session)


def get_media_service() -> MediaService:
    return MediaService()


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    role = current_user.role
    role_value = role.value if hasattr(role, "value") else str(role)
    if not has_role(UserRole(role_value), UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def get_current_active_admin_or_world_builder(
    current_user: User = Depends(get_current_user),
) -> User:
    role = current_user.role
    role_value = role.value if hasattr(role, "value") else str(role)
    if not has_role(UserRole(role_value), UserRole.WORLD_BUILDER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or world builder privileges required",
        )
    return current_user
