"""Repository for Elder chat data access."""

from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.elder_chat import ElderChat, ElderChatHistory


class ElderChatRepository:
    """Repository for managing Elder chats."""

    def __init__(self, session: AsyncSession):
        """Initialize repository with database session."""
        self.session = session

    async def create_chat(
        self,
        user_id: int,
        agent_id: str,
        name: str,
        color: Optional[str] = None,
    ) -> ElderChat:
        """Create a new chat."""
        chat = ElderChat(
            user_id=user_id,
            agent_id=agent_id,
            name=name,
            color=color,
        )
        self.session.add(chat)
        await self.session.flush()
        await self.session.refresh(chat)
        return chat

    async def get_by_id(self, chat_id: str) -> Optional[ElderChat]:
        """Get chat by ID."""
        result = await self.session.execute(
            select(ElderChat).where(ElderChat.id == chat_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_history(
        self, chat_id: str, limit: int = 50
    ) -> Optional[ElderChat]:
        """Get chat by ID with history (limited to most recent messages)."""
        result = await self.session.execute(
            select(ElderChat)
            .where(ElderChat.id == chat_id)
            .options(joinedload(ElderChat.history))
        )
        chat = result.unique().scalar_one_or_none()
        
        if chat and chat.history:
            # Sort history by created_at descending and limit
            chat.history = sorted(
                chat.history, key=lambda h: h.created_at, reverse=True
            )[:limit]
            # Reverse to get chronological order for the limited set
            chat.history.reverse()
        
        return chat

    async def list_user_chats(
        self,
        user_id: int,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ElderChat]:
        """List chats for a user, optionally filtered by agent."""
        query = select(ElderChat).where(ElderChat.user_id == user_id)
        
        if agent_id:
            query = query.where(ElderChat.agent_id == agent_id)
        
        query = query.order_by(ElderChat.updated_at.desc()).limit(limit).offset(offset)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_user_chats_for_agent(self, user_id: int, agent_id: str) -> int:
        """Count how many chats a user has with a specific agent."""
        result = await self.session.execute(
            select(func.count(ElderChat.id)).where(
                and_(ElderChat.user_id == user_id, ElderChat.agent_id == agent_id)
            )
        )
        return result.scalar_one()

    async def update_chat(
        self,
        chat_id: str,
        name: Optional[str] = None,
        color: Optional[str] = None,
    ) -> Optional[ElderChat]:
        """Update chat metadata."""
        chat = await self.get_by_id(chat_id)
        if not chat:
            return None
        
        if name is not None:
            chat.name = name
        if color is not None:
            chat.color = color
        
        await self.session.flush()
        await self.session.refresh(chat)
        return chat

    async def delete_chat(self, chat_id: str) -> bool:
        """Delete a chat and its history."""
        chat = await self.get_by_id(chat_id)
        if not chat:
            return False
        
        await self.session.delete(chat)
        await self.session.flush()
        return True

    async def add_message(
        self, chat_id: str, role: str, content: str
    ) -> ElderChatHistory:
        """Add a message to chat history."""
        message = ElderChatHistory(
            chat_id=chat_id,
            role=role,
            content=content,
        )
        self.session.add(message)
        
        # Update chat's updated_at timestamp
        chat = await self.get_by_id(chat_id)
        if chat:
            # Just accessing and modifying will trigger onupdate
            await self.session.flush()
        
        await self.session.refresh(message)
        return message

    async def get_chat_history(
        self, chat_id: str, limit: int = 50, offset: int = 0
    ) -> list[ElderChatHistory]:
        """Get chat history with pagination."""
        query = (
            select(ElderChatHistory)
            .where(ElderChatHistory.chat_id == chat_id)
            .order_by(ElderChatHistory.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_chat_history(self, chat_id: str) -> int:
        """Count total messages in a chat."""
        result = await self.session.execute(
            select(func.count(ElderChatHistory.id)).where(
                ElderChatHistory.chat_id == chat_id
            )
        )
        return result.scalar_one()
