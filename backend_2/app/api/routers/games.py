from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import (
    get_current_user,
    get_game_service,
    get_notification_service,
    require_roles,
)
from app.models.game import Game, GameSession, GameSessionPoll
from app.models.notification import NotificationAuthorType, NotificationType
from app.models.user import User, UserRole
from app.schemas.game import (
    AttendanceRequest,
    GameCreate,
    GameMemberSummary,
    GameRead,
    GameSessionCreate,
    GameSessionPollCreate,
    GameSessionPollDetailRead,
    GameSessionPollRead,
    GameSessionPollOptionRead,
    GameSessionRead,
    GameSessionUpdate,
    GameUpdate,
    PollFinalizeRequest,
    PollVoteRequest,
)
from app.services.game_service import GameService
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/games", tags=["games"])


def _sanitize_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


async def _get_game_or_404(game_id: int, service: GameService) -> Game:
    game = await service.get_game(game_id)
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Game not found"
        )
    return game


async def _get_session_or_404(
    game_id: int, session_id: int, service: GameService
) -> GameSession:
    session = await service.get_session(game_id, session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


async def _get_poll_or_404(
    session_id: int, poll_id: int, service: GameService
) -> GameSessionPoll:
    poll = await service.get_poll(session_id, poll_id)
    if not poll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Poll not found"
        )
    return poll


def _ensure_member(game: Game, user: User) -> None:
    if user.role in {UserRole.ADMIN, UserRole.WORLD_BUILDER}:
        return
    member_ids = {member.id for member in game.members}
    if user.id not in member_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this game",
        )


def _serialize_session(session: GameSession) -> GameSessionRead:
    attendance_payload = [
        {
            "user_id": entry.user_id,
            "attending": entry.attending,
            "responded_at": entry.responded_at,
        }
        for entry in getattr(session, "attendance", []) or []
    ]

    polls_payload = []
    for poll in getattr(session, "polls", []) or []:
        options_payload = [
            {
                "id": option.id,
                "proposed_start": option.proposed_start,
                "vote_count": len(getattr(option, "votes", []) or []),
            }
            for option in getattr(poll, "options", []) or []
        ]
        polls_payload.append(
            {
                "id": poll.id,
                "created_at": poll.created_at,
                "is_finalized": poll.is_finalized,
                "finalized_option_id": poll.finalized_option_id,
                "options": options_payload,
            }
        )

    data = {
        "id": session.id,
        "game_id": session.game_id,
        "title": session.title,
        "scheduled_date": session.scheduled_date,
        "location": session.location,
        "summary": session.summary,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "attendance": attendance_payload,
        "polls": polls_payload,
    }
    return GameSessionRead.model_validate(data)


def _serialize_poll(poll: GameSessionPoll) -> GameSessionPollRead:
    options = []
    for option in poll.options:
        votes = getattr(option, "votes", [])
        vote_count = len(votes) if votes is not None else 0
        options.append(GameSessionPollOptionRead.model_validate({
            "id": option.id,
            "proposed_start": option.proposed_start,
            "vote_count": vote_count,
        }))
    payload = GameSessionPollRead.model_validate(poll)
    payload.options = options
    return payload


def _serialize_poll_detail(poll: GameSessionPoll) -> GameSessionPollDetailRead:
    """Serialize poll with full vote details."""
    from app.schemas.game import GameSessionPollOptionDetailRead

    options_detail = []
    for option in poll.options:
        # Leverage from_attributes=True to use ORM attributes directly
        option_detail = GameSessionPollOptionDetailRead.model_validate(option)
        options_detail.append(option_detail)

    # Use from_attributes=True and update the options field
    poll_detail = GameSessionPollDetailRead.model_validate(poll)
    poll_detail.options = options_detail
    return poll_detail


async def _notify_members(
    notification_service: NotificationService,
    *,
    game: Game,
    author: User,
    title: str,
    description: str,
    notification_type: NotificationType = NotificationType.SESSION_UPDATES,
) -> None:
    for member in game.members:
        await notification_service.create_notification(
            {
                "user_id": member.id,
                "notification_type": notification_type.value,
                "title": title,
                "description": description,
                "author_type": NotificationAuthorType.USER.value,
                "author_id": str(author.id),
                "send_email": False,
            }
        )


@router.post(
    "/",
    response_model=GameRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_game(
    payload: GameCreate,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameRead:
    try:
        game = await service.create_game(
            payload.model_dump(exclude={"member_ids"}), payload.member_ids
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return GameRead.model_validate(game)


@router.get(
    "/",
    response_model=list[GameRead],
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER))],
)
async def list_games(
    skip: int = 0,
    limit: int = 50,
    name: str | None = None,
    service: GameService = Depends(get_game_service),
) -> list[GameRead]:
    games = await service.list_games(skip=skip, limit=limit, name=name)
    return [GameRead.model_validate(game) for game in games]


@router.get("/mine", response_model=list[GameRead])
async def list_my_games(
    current_user: User = Depends(get_current_user),
    service: GameService = Depends(get_game_service),
) -> list[GameRead]:
    games = await service.list_games_for_user(current_user.id)
    return [GameRead.model_validate(game) for game in games]


@router.get(
    "/{game_id}",
    response_model=GameRead,
)
async def get_game(
    game_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> GameRead:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    return GameRead.model_validate(game)


@router.get(
    "/{game_id}/members",
    response_model=list[GameMemberSummary],
)
async def list_members(
    game_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> list[GameMemberSummary]:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    return [GameMemberSummary.model_validate(member) for member in game.members]


@router.put(
    "/{game_id}",
    response_model=GameRead,
)
async def update_game(
    game_id: int,
    payload: GameUpdate,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameRead:
    game = await _get_game_or_404(game_id, service)
    try:
        updated = await service.update_game(
            game,
            payload.model_dump(
                exclude_unset=True,
                exclude={"member_ids"},
            ),
            member_ids=payload.member_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return GameRead.model_validate(updated)


@router.delete(
    "/{game_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_game(
    game_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    game = await _get_game_or_404(game_id, service)
    await service.delete_game(game)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{game_id}/members",
    response_model=GameRead,
)
async def add_members(
    game_id: int,
    member_ids: list[int],
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameRead:
    game = await _get_game_or_404(game_id, service)
    try:
        updated = await service.add_members(game, member_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return GameRead.model_validate(updated)


@router.delete(
    "/{game_id}/members/{user_id}",
    response_model=GameRead,
)
async def remove_member(
    game_id: int,
    user_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameRead:
    game = await _get_game_or_404(game_id, service)
    updated = await service.remove_member(game, user_id)
    return GameRead.model_validate(updated)


@router.post(
    "/{game_id}/sessions",
    response_model=GameSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    game_id: int,
    payload: GameSessionCreate,
    service: GameService = Depends(get_game_service),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameSessionRead:
    game = await _get_game_or_404(game_id, service)
    session = await service.create_session(game, payload.model_dump())
    await _notify_members(
        notification_service,
        game=game,
        author=current_user,
        title=f"Session scheduled: {session.title}",
        description=f"A new session has been created for {game.name}.",
    )
    session = await service.get_session(game.id, session.id) or session
    return _serialize_session(session)


@router.get(
    "/{game_id}/sessions",
    response_model=list[GameSessionRead],
)
async def list_sessions(
    game_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> list[GameSessionRead]:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    sessions = await service.list_sessions_for_game(game.id)
    return [_serialize_session(session) for session in sessions]


@router.get(
    "/{game_id}/sessions/{session_id}",
    response_model=GameSessionRead,
)
async def get_session(
    game_id: int,
    session_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> GameSessionRead:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    session = await _get_session_or_404(game.id, session_id, service)
    return _serialize_session(session)


@router.put(
    "/{game_id}/sessions/{session_id}",
    response_model=GameSessionRead,
)
async def update_session(
    game_id: int,
    session_id: int,
    payload: GameSessionUpdate,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameSessionRead:
    session = await _get_session_or_404(game_id, session_id, service)
    updated = await service.update_session(
        session, payload.model_dump(exclude_unset=True)
    )
    return _serialize_session(updated)


@router.post(
    "/{game_id}/sessions/{session_id}/attendance",
    response_model=GameSessionRead,
)
async def set_attendance(
    game_id: int,
    session_id: int,
    payload: AttendanceRequest,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> GameSessionRead:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    session = await _get_session_or_404(game.id, session_id, service)
    await service.set_attendance(session, current_user.id, payload.attending)
    updated = await service.get_session(game.id, session_id)
    assert updated is not None
    return _serialize_session(updated)


@router.post(
    "/{game_id}/sessions/{session_id}/polls",
    response_model=GameSessionPollRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_poll(
    game_id: int,
    session_id: int,
    payload: GameSessionPollCreate,
    service: GameService = Depends(get_game_service),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameSessionPollRead:
    game = await _get_game_or_404(game_id, service)
    session = await _get_session_or_404(game.id, session_id, service)
    options = [option.model_dump() for option in payload.options]
    if not options:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one poll option must be provided",
        )
    try:
        poll = await service.create_poll(session, options)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await _notify_members(
        notification_service,
        game=game,
        author=current_user,
        title=f"Session poll opened: {session.title}",
        description=f"A poll for scheduling session '{session.title}' is now available.",
    )
    poll = await service.get_poll(session.id, poll.id) or poll
    return _serialize_poll(poll)


@router.get(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}",
    response_model=GameSessionPollDetailRead,
)
async def get_poll_details(
    game_id: int,
    session_id: int,
    poll_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> GameSessionPollDetailRead:
    """Get detailed voting information for a specific session poll."""
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    session = await _get_session_or_404(game.id, session_id, service)
    poll = await _get_poll_or_404(session.id, poll_id, service)
    return _serialize_poll_detail(poll)


@router.post(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}/options",
    response_model=GameSessionPollRead,
)
async def add_poll_option(
    game_id: int,
    session_id: int,
    poll_id: int,
    payload: GameSessionPollCreate,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameSessionPollRead:
    _ = await _get_game_or_404(game_id, service)
    _ = await _get_session_or_404(game_id, session_id, service)
    poll = await _get_poll_or_404(session_id, poll_id, service)
    if poll.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Poll already finalized",
        )
    for option in payload.options:
        await service.add_poll_option(poll, option.model_dump())
    poll = await service.get_poll(session_id, poll_id) or poll
    return _serialize_poll(poll)


@router.post(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}/vote",
    response_model=GameSessionPollRead,
)
async def vote_poll(
    game_id: int,
    session_id: int,
    poll_id: int,
    payload: PollVoteRequest,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(get_current_user),
) -> GameSessionPollRead:
    game = await _get_game_or_404(game_id, service)
    _ensure_member(game, current_user)
    session = await _get_session_or_404(game.id, session_id, service)
    poll = await _get_poll_or_404(session.id, poll_id, service)
    if poll.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Poll is already finalized",
        )
    option = next((item for item in poll.options if item.id == payload.option_id), None)
    if option is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Option not found"
        )
    try:
        await service.vote_poll_option(poll, option, current_user.id)
    except Exception as exc:  # pragma: no cover - unique constraint path
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vote already recorded for this option",
        ) from exc
    # Fetch a fresh poll instance to ensure votes are loaded correctly
    poll = await service.get_poll(session.id, poll_id) or poll
    return _serialize_poll(poll)


@router.post(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}/finalize",
    response_model=GameSessionRead,
)
async def finalize_poll(
    game_id: int,
    session_id: int,
    poll_id: int,
    payload: PollFinalizeRequest,
    service: GameService = Depends(get_game_service),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> GameSessionRead:
    game = await _get_game_or_404(game_id, service)
    session = await _get_session_or_404(game.id, session_id, service)
    poll = await _get_poll_or_404(session.id, poll_id, service)
    if poll.is_finalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Poll already finalized",
        )
    option = next((item for item in poll.options if item.id == payload.option_id), None)
    if option is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Option not found"
        )
    # Capture the proposed_start before the service call to avoid SQLAlchemy detached instance access
    proposed_start = option.proposed_start
    finalized_poll, attendees = await service.finalize_poll(session, poll, option)
    await _notify_members(
        notification_service,
        game=game,
        author=current_user,
        title=f"Session date selected: {session.title}",
        description=(
            f"Session '{session.title}' is scheduled for {proposed_start.isoformat()}."
        ),
    )
    updated_session = await service.get_session(game.id, session.id)
    assert updated_session is not None
    return _serialize_session(updated_session)


@router.delete(
    "/{game_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_session(
    game_id: int,
    session_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    """Delete a game session (admin/world_builder only)."""
    _ = await _get_game_or_404(game_id, service)
    session = await _get_session_or_404(game_id, session_id, service)
    await service.delete_session(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_poll(
    game_id: int,
    session_id: int,
    poll_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    """Delete a poll session (admin/world_builder only)."""
    _ = await _get_game_or_404(game_id, service)
    _ = await _get_session_or_404(game_id, session_id, service)
    poll = await _get_poll_or_404(session_id, poll_id, service)
    await service.delete_poll(poll)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{game_id}/sessions/{session_id}/polls/{poll_id}/votes/{user_id}/{option_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_vote(
    game_id: int,
    session_id: int,
    poll_id: int,
    user_id: int,
    option_id: int,
    service: GameService = Depends(get_game_service),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.WORLD_BUILDER)),
) -> Response:
    """Delete a specific vote from a poll (admin/world_builder only)."""
    _ = await _get_game_or_404(game_id, service)
    _ = await _get_session_or_404(game_id, session_id, service)
    poll = await _get_poll_or_404(session_id, poll_id, service)
    option = next((item for item in poll.options if item.id == option_id), None)
    if option is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Option not found"
        )
    await service.unvote_poll_option(option, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
