"""API router for Elder chat management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from app.jobs.elder.chat_schemas import (
    ElderChatCreate,
    ElderChatListResponse,
    ElderChatResponse,
    ElderChatUpdate,
    ElderChatWithHistoryResponse,
)
from app.models.user import User
from app.services.elder_chat_service import ElderChatService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs/elder/chats", tags=["elder-chats"])


@router.post("/", response_model=ElderChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat_data: ElderChatCreate,
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> ElderChatResponse:
    """
    Create a new chat with an elder agent.

    Users can create up to 10 chats per agent.
    """
    service = ElderChatService(db_session)

    try:
        chat = await service.create_chat(user_id=current_user.id, chat_data=chat_data)
        return chat
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/", response_model=ElderChatListResponse)
async def list_chats(
    agent_id: str | None = Query(None, description="Filter by agent ID"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> ElderChatListResponse:
    """
    List all chats for the current user.

    Optionally filter by agent_id.
    """
    service = ElderChatService(db_session)
    chats = await service.list_user_chats(
        user_id=current_user.id,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
    )
    return chats


@router.get("/{chat_id}", response_model=ElderChatWithHistoryResponse)
async def get_chat(
    chat_id: str,
    include_history: bool = Query(
        False, description="Include chat history in response"
    ),
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> ElderChatWithHistoryResponse:
    """
    Get a specific chat by ID.

    Users can only access their own chats.
    """
    service = ElderChatService(db_session)
    chat = await service.get_chat(
        chat_id=chat_id, user_id=current_user.id, include_history=include_history
    )

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    return chat


@router.patch("/{chat_id}", response_model=ElderChatResponse)
async def update_chat(
    chat_id: str,
    chat_update: ElderChatUpdate,
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> ElderChatResponse:
    """
    Update chat metadata (name, color).

    Users can only update their own chats.
    """
    service = ElderChatService(db_session)
    chat = await service.update_chat(
        chat_id=chat_id, user_id=current_user.id, chat_update=chat_update
    )

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )

    return chat


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> None:
    """
    Delete a chat and all its history.

    Users can only delete their own chats.
    """
    service = ElderChatService(db_session)
    success = await service.delete_chat(chat_id=chat_id, user_id=current_user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found",
        )


@router.get("/{chat_id}/file", response_model=dict)
async def get_chat_file(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Retrieve the filesystem chat history JSON for a chat.

    Returns a JSON object with keys: user_id, agent_id, chat_id, created_at, messages[].
    """
    service = ElderChatService(db_session)
    data = await service.read_chat_file(chat_id=chat_id, user_id=current_user.id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat file not found")
    return data
