"""Service layer for Elder chat business logic."""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.elder.chat_schemas import (
    ElderChatCreate,
    ElderChatListResponse,
    ElderChatResponse,
    ElderChatUpdate,
    ElderChatWithHistoryResponse,
)
from app.repositories.agent_repository import AgentRepository
from app.repositories.elder_chat_repository import ElderChatRepository

logger = logging.getLogger(__name__)

MAX_CHATS_PER_AGENT = 10


class ElderChatService:
    """Service for managing Elder chats."""

    def __init__(self, session: AsyncSession):
        """Initialize service with database session."""
        self.session = session
        self.chat_repo = ElderChatRepository(session)
        self.agent_repo = AgentRepository(session)

    async def create_chat(
        self, user_id: int, chat_data: ElderChatCreate
    ) -> ElderChatResponse:
        """
        Create a new chat.

        Validates:
        - Agent exists and is active
        - User hasn't exceeded max chats per agent (10)
        """
        # Check if agent exists and is active
        agent = await self.agent_repo.get_by_id(chat_data.agent_id)
        if not agent:
            raise ValueError(f"Agent with ID {chat_data.agent_id} not found")
        if not agent.active:
            raise ValueError(f"Agent {agent.name} is not active")

        # Check chat limit
        chat_count = await self.chat_repo.count_user_chats_for_agent(
            user_id, chat_data.agent_id
        )
        if chat_count >= MAX_CHATS_PER_AGENT:
            raise ValueError(
                f"Maximum of {MAX_CHATS_PER_AGENT} chats per agent reached. Please delete a chat before creating a new one."
            )

        # Create chat
        chat = await self.chat_repo.create_chat(
            user_id=user_id,
            agent_id=chat_data.agent_id,
            name=chat_data.name,
            color=chat_data.color,
        )

        await self.session.commit()

        logger.info(
            f"Created chat {chat.id} for user {user_id} with agent {chat_data.agent_id}"
        )

        return ElderChatResponse.model_validate(chat)

    async def list_user_chats(
        self,
        user_id: int,
        agent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ElderChatListResponse:
        """List chats for a user."""
        chats = await self.chat_repo.list_user_chats(
            user_id=user_id, agent_id=agent_id, limit=limit, offset=offset
        )

        # Get message count for each chat
        chat_responses = []
        for chat in chats:
            count = await self.chat_repo.count_chat_history(chat.id)
            chat_response = ElderChatResponse.model_validate(chat)
            chat_response.message_count = count
            chat_responses.append(chat_response)

        # Count total (for pagination info)
        # For simplicity, we'll just use the length of results
        # In production, you might want a separate count query
        total = len(chat_responses)

        return ElderChatListResponse(
            chats=chat_responses, total=total, limit=limit, offset=offset
        )

    async def get_chat(
        self, chat_id: str, user_id: int, include_history: bool = False
    ) -> Optional[ElderChatWithHistoryResponse]:
        """
        Get chat by ID.

        Validates that the chat belongs to the user.
        """
        if include_history:
            chat = await self.chat_repo.get_by_id_with_history(chat_id)
        else:
            chat = await self.chat_repo.get_by_id(chat_id)

        if not chat:
            return None

        # Security check: ensure user owns this chat
        if chat.user_id != user_id:
            return None

        if include_history:
            return ElderChatWithHistoryResponse.model_validate(chat)
        else:
            count = await self.chat_repo.count_chat_history(chat.id)
            response = ElderChatResponse.model_validate(chat)
            response.message_count = count
            return response

    async def update_chat(
        self, chat_id: str, user_id: int, chat_update: ElderChatUpdate
    ) -> Optional[ElderChatResponse]:
        """
        Update chat metadata.

        Validates that the chat belongs to the user.
        """
        # First check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return None

        # Update chat
        updated_chat = await self.chat_repo.update_chat(
            chat_id=chat_id, name=chat_update.name, color=chat_update.color
        )

        if updated_chat:
            await self.session.commit()
            logger.info(f"Updated chat {chat_id}")
            count = await self.chat_repo.count_chat_history(chat_id)
            response = ElderChatResponse.model_validate(updated_chat)
            response.message_count = count
            return response

        return None

    async def delete_chat(self, chat_id: str, user_id: int) -> bool:
        """
        Delete a chat.

        Validates that the chat belongs to the user.
        """
        # Check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return False

        # Delete chat
        success = await self.chat_repo.delete_chat(chat_id)

        if success:
            await self.session.commit()
            logger.info(f"Deleted chat {chat_id} for user {user_id}")

        return success

    async def add_message_to_chat(
        self, chat_id: str, user_id: int, role: str, content: str
    ) -> bool:
        """
        Add a message to chat history.

        Validates that the chat belongs to the user.
        """
        # Check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return False

        # Add message
        await self.chat_repo.add_message(chat_id=chat_id, role=role, content=content)
        await self.session.commit()

        return True

    async def get_chat_history_for_context(
        self, chat_id: str, user_id: int, limit: int = 20
    ) -> Optional[list[dict[str, str]]]:
        """
        Get chat history formatted for LLM context.

        Returns:
            - None if chat doesn't exist or doesn't belong to user
            - Empty list if chat exists but has no messages
            - List of {"role": "user"/"assistant", "content": "..."} dicts otherwise
        """
        # Check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return None

        # Get recent history
        total_messages = await self.chat_repo.count_chat_history(chat_id)
        offset = max(0, total_messages - limit)

        history = await self.chat_repo.get_chat_history(
            chat_id=chat_id, limit=limit, offset=offset
        )

        return [{"role": msg.role, "content": msg.content} for msg in history]
