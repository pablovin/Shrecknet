"""Short-lived turn-bound retrieval grants, distinct from ordinary access tokens."""
import time
from jose import jwt, JWTError
from app.core.config_store import get_settings
from app.core.security import _resolve_keypair
from app.models.user import User
from app.models.character_companion import CompanionTurn, CompanionChat
from app.repositories.character_companion_repository import fail
from app.schemas.character_companion import TERMINAL

AUDIENCE = "shrecknet-companion-retrieval"


async def active_principal(session, turn):
    if turn.principal["expires_at"] <= time.time():
        fail(401, "originating_access_expired")
    user = await session.get(User, turn.user_id)
    if user is None:
        fail(401, "invalid_user")
    if turn.principal.get("sid"):
        from app.services.auth_sessions import BrowserSessionService
        from app.core.browser_auth import get_browser_auth_settings
        await BrowserSessionService(session, get_browser_auth_settings()).active(turn.principal["sid"], user.id)
    return user


def grant(turn, tool):
    private, _ = _resolve_keypair()
    s = get_settings()
    return jwt.encode({"iss": s.jwt_issuer, "aud": AUDIENCE, "sub": str(turn.user_id), "turn_id": turn.id,
                       "tool": tool, "iat": int(time.time()), "exp": min(int(time.time()) + 60, turn.principal["expires_at"])},
                      private, algorithm="RS256", headers={"kid": s.jwt_kid})


async def authorize(session, token, tool):
    _, public = _resolve_keypair()
    try:
        claims = jwt.decode(token, public, algorithms=["RS256"], audience=AUDIENCE, issuer=get_settings().jwt_issuer)
    except JWTError:
        fail(401, "invalid_retrieval_grant")
    if claims.get("tool") != tool:
        fail(403, "retrieval_scope_mismatch")
    turn = await session.get(CompanionTurn, claims.get("turn_id"))
    if not turn or str(turn.user_id) != claims.get("sub") or turn.status in TERMINAL:
        fail(403, "retrieval_turn_unavailable")
    chat = await session.get(CompanionChat, turn.chat_id)
    if not chat or chat.deleted:
        fail(403, "retrieval_turn_unavailable")
    user = await active_principal(session, turn)
    from app.graph.neo4j import get_driver
    from app.services.character_agent_service import CharacterAgentService
    async with get_driver().session(database=get_settings().neo4j_database) as graph:
        await CharacterAgentService(None,graph).ensure_queryable(turn.character_agent_id,public_only=getattr(user.role,"value",user.role)!="admin")
    return {"user_id": user.id, "role": getattr(user.role, "value", user.role), "scope": turn.request.get("context") or {}, "turn_id": turn.id}
