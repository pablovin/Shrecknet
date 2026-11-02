"""Service layer for Elder chat business logic."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.elder.chat_schemas import (
    ElderChatCreate,
    ElderChatListResponse,
    ElderChatResponse,
    ElderChatUpdate,
    ElderChatWithHistoryResponse,
)
from app.models.elder_chat import ElderChat
from app.repositories.agent_repository import AgentRepository
from app.repositories.elder_chat_repository import ElderChatRepository
from app.jobs.elder.scope import ElderWorldScope
from app.services.elder_scope_service import ElderScopeService
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

    @staticmethod
    def storage_agent(chat: ElderChat) -> str | None:
        return getattr(chat, "legacy_storage_agent_id", None) or chat.agent_id

    @staticmethod
    def chat_response(chat: ElderChat) -> ElderChatResponse:
        response = ElderChatResponse.model_validate(chat)
        response.read_only = not bool(getattr(chat, "scope_mode", None))
        return response

    async def read_messages(self, chat: ElderChat, user_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        stored = read_chat(user_id, self.storage_agent(chat), chat.id)
        if stored is not None:
            messages = stored.get("messages", [])
            return messages[-limit:] if limit is not None else messages
        # Older SQL-only conversations must remain readable after migration.
        historical = await self.chat_repo.get_by_id_with_history(chat.id)
        rows = sorted(historical.history if historical else [], key=lambda row: row.created_at)
        if limit is not None:
            rows = rows[-limit:]
        return [{"role": row.role, "content": row.content, "ts": row.created_at.isoformat()} for row in rows]

    async def create_chat(
        self, user_id: int, chat_data: ElderChatCreate
    ) -> ElderChatResponse:
        """
        Create a new chat.

        Scoped chats use the built-in Elder and a caller-selected world snapshot.
        Legacy agent creation remains a compatibility path. Each identity permits
        ten conversations; legacy histories do not count against the singleton.
        """
        ontology_ids = None
        if chat_data.world_scope is not None:
            if chat_data.agent_id is not None:
                raise ValueError("Provide world_scope or legacy agent_id, not both")
            ontology_ids = await ElderScopeService(self.session).resolve(chat_data.world_scope)
        else:
            if not chat_data.agent_id:
                raise ValueError("Select one or more worlds")
            agent = await self.agent_repo.get_by_id(chat_data.agent_id)
            if not agent or not agent.active or agent.job != "elder":
                raise ValueError("Active Elder agent not found")
        chat_count = await self.chat_repo.count_user_chats_for_agent(user_id, chat_data.agent_id)
        if chat_count >= MAX_CHATS_PER_AGENT:
            raise ValueError("Maximum of 10 conversations reached. Delete a conversation before creating another.")

        # Create chat
        chat = await self.chat_repo.create_chat(
            user_id=user_id,
            agent_id=chat_data.agent_id,
            name=chat_data.name,
            color=chat_data.color,
            scope_mode=chat_data.world_scope.mode if chat_data.world_scope else None,
            ontology_ids=ontology_ids,
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

        return self.chat_response(chat)

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
            chat_response = self.chat_response(chat)
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

        if getattr(chat, "scope_mode", None) and chat.ontology_ids:
            await ElderScopeService(self.session).resolve(ElderWorldScope(mode="selected", ontology_ids=chat.ontology_ids))

        if include_history:
            messages = await self.read_messages(chat, user_id)
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

            payload = ElderChatWithHistoryResponse.model_validate({**self.chat_response(chat).model_dump(), "history": parsed_history})
            payload.read_only = not bool(getattr(chat, "scope_mode", None))
            payload.message_count = len(parsed_history)
            return payload

        count = await self.chat_repo.count_chat_history(chat.id)
        if count == 0:
            file_data = read_chat(user_id, self.storage_agent(chat), chat_id) or {}
            count = len(file_data.get("messages", []))
        response = self.chat_response(chat)
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
            response = self.chat_response(updated_chat)
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
                delete_chat_file(user_id, self.storage_agent(chat), chat_id)
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
            append_message(user_id, self.storage_agent(chat), chat_id, role, content, metadata)
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
        if getattr(chat, "scope_mode", None) and chat.ontology_ids:
            await ElderScopeService(self.session).resolve(ElderWorldScope(mode="selected", ontology_ids=chat.ontology_ids))
        return read_chat(user_id, self.storage_agent(chat), chat_id)

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

        recent_messages = await self.read_messages(chat, user_id, limit)

        return [{"role": msg["role"], "content": msg["content"]} for msg in recent_messages]
