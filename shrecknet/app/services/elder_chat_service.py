"""Service layer for Elder chat business logic."""

import logging
from datetime import datetime, timezone
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
from app.utils.chat_store import append_message, delete_chat_file, init_chat_file, read_chat

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
        chat_id = chat.id
        agent_id = chat.agent_id

        await self.session.commit()

        # Initialize filesystem chat log
        try:
            init_chat_file(user_id, agent_id, chat_id)
        except Exception:
            logger.warning(
                "Failed to init chat file for chat_id=%s user_id=%s agent_id=%s",
                chat_id,
                user_id,
                agent_id,
            )

        logger.info(
            f"Created chat {chat_id} for user {user_id} with agent {chat_data.agent_id}"
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
        chat = await self.chat_repo.get_by_id(chat_id)

        if not chat:
            return None

        # Security check: ensure user owns this chat
        if chat.user_id != user_id:
            return None

        if include_history:
            file_data = read_chat(user_id, chat.agent_id, chat_id) or {}
            messages = file_data.get("messages", [])
            parsed_history = []
            for idx, msg in enumerate(messages, start=1):
                ts_raw = msg.get("ts")
                try:
                    created_at = (
                        datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        if isinstance(ts_raw, str)
                        else datetime.now(timezone.utc)
                    )
                except Exception:
                    created_at = datetime.now(timezone.utc)
                parsed_history.append(
                    {
                        "id": idx,
                        "chat_id": chat_id,
                        "role": msg.get("role", "assistant"),
                        "content": msg.get("content", ""),
                        "created_at": created_at,
                        "metadata": msg.get("meta"),
                    }
                )

            payload = ElderChatWithHistoryResponse.model_validate(chat)
            payload.history = parsed_history
            payload.message_count = len(parsed_history)
            return payload

        count = await self.chat_repo.count_chat_history(chat.id)
        if count == 0:
            file_data = read_chat(user_id, chat.agent_id, chat_id) or {}
            count = len(file_data.get("messages", []))
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
            try:
                delete_chat_file(user_id, chat.agent_id, chat_id)
            except Exception:
                logger.warning("Failed to delete chat file for %s", chat_id)

        return success

    async def add_message_to_chat(
        self,
        chat_id: str,
        user_id: int,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> bool:
        """
        Add a message to chat history.

        Validates that the chat belongs to the user.
        """
        # Check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return False

        # Append to filesystem log (source of truth for history)
        try:
            append_message(user_id, chat.agent_id, chat_id, role, content, metadata)
        except Exception as exc:
            logger.warning(
                "Failed to append chat file for %s: %s", chat_id, exc, exc_info=True
            )

        # Optionally skip DB history persistence to rely solely on JSON files
        return True

    async def read_chat_file(self, chat_id: str, user_id: int) -> dict | None:
        """Read chat history JSON from filesystem if available."""
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return None
        return read_chat(user_id, chat.agent_id, chat_id)

    async def get_chat_history_for_context(
        self, chat_id: str, user_id: int, limit: int = 20
    ) -> Optional[list[dict[str, str]]]:
        """
        Get chat history formatted for LLM context from JSON files.

        Returns:
            - None if chat doesn't exist or doesn't belong to user
            - Empty list if chat exists but has no messages
            - List of {"role": "user"/"assistant", "content": "..."} dicts otherwise
        """
        # Check if chat exists and belongs to user
        chat = await self.chat_repo.get_by_id(chat_id)
        if not chat or chat.user_id != user_id:
            return None

        # Read from JSON file (source of truth for chat history)
        chat_data = read_chat(user_id, chat.agent_id, chat_id)
        if not chat_data or "messages" not in chat_data:
            return []

        messages = chat_data["messages"]
        # Get the last 'limit' messages
        recent_messages = messages[-limit:] if len(messages) > limit else messages

        return [{"role": msg["role"], "content": msg["content"]} for msg in recent_messages]
