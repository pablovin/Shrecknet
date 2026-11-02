"""Resolve caller-selected scopes using the platform's authenticated world audience."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.jobs.elder.scope import ElderWorldScope
from app.models.ontology import Ontology
from app.repositories.elder_chat_repository import ElderChatRepository


class ElderScopeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def resolve(self, scope: ElderWorldScope) -> list[int]:
        # Existing world discovery/read policy permits all authenticated viewers.
        # Perspective-level visibility remains enforced by the retrieval projection.
        available = set(
            (await self.session.execute(select(Ontology.id))).scalars().all()
        )
        chosen = available if scope.mode == "all" else set(scope.ontology_ids)
        if not chosen:
            raise HTTPException(status_code=400, detail="No worlds available")
        if not chosen.issubset(available):
            raise HTTPException(status_code=404, detail="World not found")
        return sorted(chosen)

    async def query_worlds(
        self, user_id: int, chat_id: str | None, requested: ElderWorldScope | None
    ) -> list[int]:
        if chat_id is None:
            if requested is None:
                raise HTTPException(
                    status_code=422, detail="world_scope is required without a chat"
                )
            return await self.resolve(requested)
        chat = await ElderChatRepository(self.session).get_by_id(chat_id)
        if chat is None or chat.user_id != user_id:
            raise HTTPException(status_code=404, detail="Chat not found")
        if not getattr(chat, "scope_mode", None) or not chat.ontology_ids:
            raise HTTPException(
                status_code=409,
                detail="Legacy conversation is read-only; start a scoped conversation",
            )
        ids = await self.resolve(
            ElderWorldScope(mode="selected", ontology_ids=chat.ontology_ids)
        )
        if requested is not None and (
            requested.mode != chat.scope_mode or await self.resolve(requested) != ids
        ):
            raise HTTPException(
                status_code=409, detail="Changing worlds requires a new conversation"
            )
        return ids
