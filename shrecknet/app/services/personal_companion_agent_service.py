"""Service layer for personal companion agent business logic."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.personal_companion_agent_repository import (
    PersonalCompanionAgentRepository,
)
from app.schemas.personal_companion_agent import (
    PersonalCompanionAgentCreate,
    PersonalCompanionAgentRead,
    PersonalCompanionAgentUpdate,
)


class PersonalCompanionAgentService:
    """Service for managing user-scoped companion agents."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = PersonalCompanionAgentRepository(session)

    async def create_for_user(
        self, user_id: int, payload: PersonalCompanionAgentCreate
    ) -> PersonalCompanionAgentRead:
        """Create a companion for the user if one does not exist."""
        existing = await self.repository.get_by_user_id(user_id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Personal companion agent already exists for this user",
            )

        try:
            companion = await self.repository.create_for_user(user_id, payload)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Personal companion agent already exists for this user",
            ) from exc

        await self.session.commit()
        refreshed = await self.repository.get_by_user_id(user_id)
        if refreshed is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal companion agent not found after create",
            )
        return PersonalCompanionAgentRead.model_validate(refreshed)

    async def get_for_user(self, user_id: int) -> PersonalCompanionAgentRead:
        """Get the companion for a user or 404 if missing."""
        companion = await self.repository.get_by_user_id(user_id)
        if companion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal companion agent not found",
            )
        return PersonalCompanionAgentRead.model_validate(companion)

    async def update_for_user(
        self, user_id: int, payload: PersonalCompanionAgentUpdate
    ) -> PersonalCompanionAgentRead:
        """Update the companion for a user."""
        companion = await self.repository.update_for_user(user_id, payload)
        if companion is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal companion agent not found",
            )

        await self.session.commit()
        refreshed = await self.repository.get_by_user_id(user_id)
        if refreshed is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal companion agent not found after update",
            )
        return PersonalCompanionAgentRead.model_validate(refreshed)

    async def delete_for_user(self, user_id: int) -> None:
        """Delete the companion for a user."""
        deleted = await self.repository.delete_for_user(user_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Personal companion agent not found",
            )
        await self.session.commit()
