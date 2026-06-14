"""Repository for PersonalCompanionAgent model."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.personal_companion_agent import PersonalCompanionAgent
from app.schemas.personal_companion_agent import (
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentUpdate,
)


class PersonalCompanionAgentRepository:
    """Repository for managing personal companion persistence."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: int) -> PersonalCompanionAgent | None:
        """Get a user's personal companion agent."""
        stmt = select(PersonalCompanionAgent).where(
            PersonalCompanionAgent.user_id == user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_for_user(
        self, user_id: int, payload: PersonalCompanionAgentCreate
    ) -> PersonalCompanionAgent:
        """Create a personal companion agent for a user."""
        companion = PersonalCompanionAgent(
            user_id=user_id,
            name=payload.name,
            avatar_url=payload.avatar_url,
            writing_style=payload.writing_style,
            active=payload.active,
        )
        self.session.add(companion)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            raise
        return companion

    async def update_for_user(
        self, user_id: int, payload: PersonalCompanionAgentUpdate
    ) -> PersonalCompanionAgent | None:
        """Update a user's personal companion agent."""
        companion = await self.get_by_user_id(user_id)
        if companion is None:
            return None

        updates = payload.model_dump(exclude_unset=True)
        for field, value in updates.items():
            setattr(companion, field, value)

        await self.session.flush()
        return companion

    async def delete_for_user(self, user_id: int) -> bool:
        """Delete a user's personal companion agent."""
        companion = await self.get_by_user_id(user_id)
        if companion is None:
            return False

        await self.session.delete(companion)
        await self.session.flush()
        return True
