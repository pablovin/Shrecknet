"""Authenticated Character Companion transport; business rules live in services."""
from __future__ import annotations
import asyncio
import json
import time
from types import SimpleNamespace
from uuid import UUID
from fastapi import BackgroundTasks, HTTPException, APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, OperationalError
from app.api.deps import get_current_user, get_db_session, oauth2_scheme
from app.api.routers.character_agents import service as character_service
from app.core.config_store import get_settings, COMPANION_MODEL_FIELDS
from app.core.security import decode_access_token
from app.models.character_companion import CompanionChat, CompanionTurn, CompanionMessage, CompanionRapport, CompanionRapportAudit, CompanionEvent
from app.repositories.character_companion_repository import fail, record, cursor, decode_cursor, utc_iso
from app.schemas.character_companion import Page, CandidateRead, SelectionRead, RapportRead, ChatRead, MessageRead, TurnQueued, TurnRead, AttachmentRead, RapportAuditRead
from app.schemas.character_companion import SelectionWrite, VersionWrite, ChatCreate, ChatUpdate, TurnCreate, TERMINAL
from app.services.character_companion_service import CharacterCompanionService
from app.services.companion_attachment_service import CompanionAttachmentService, private_root


async def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/users/me/character-companion", tags=["character-companion"], dependencies=[Depends(no_cache)])


async def service(session=Depends(get_db_session), characters=Depends(character_service)):
    try:
        yield CharacterCompanionService(session, characters)
    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError:
        await session.rollback()
        fail(409, "concurrent_change")
    except OperationalError:
        await session.rollback()
        fail(503, "database_unavailable")


async def idempotency_key(value: UUID = Header(alias="Idempotency-Key")):
    return str(value)


@router.get("/capabilities")
async def capabilities(actor=Depends(get_current_user)):
    s = get_settings()
    return {"contract_version": 1, "enabled": s.companion_enabled and s.enable_ai_agents,
            "streaming": bool(s.model_companion_synthesis.name), "images": f"{s.model_companion_synthesis.provider}/{s.model_companion_synthesis.name}" in s.companion_image_models,
            "event_versions": [1], "tools": {key: {"available": value, "reason": None if value else "not_configured"} for key, value in
                {"atlas": True, "character_memory": True, "shreckworld": bool(s.companion_shreckworld_url), "chronicle": bool(s.companion_chronicles_url), "web": bool(s.companion_web_enabled and s.companion_web_search_url)}.items()},
            "model_roles": {field: {"configured": bool(getattr(s, field).name), "available": None} for field in COMPANION_MODEL_FIELDS},
            "limits": {"image_bytes": 10485760, "images_per_turn": 4, "turn_image_bytes": 20971520, "text_characters": 8000}}


@router.get("/candidates", response_model=Page[CandidateRead])
async def candidates(ontology_id: int | None = None, search: str | None = Query(None, max_length=255),
                     cursor_value: str | None = Query(None, alias="cursor"), limit: int = Query(20, ge=1, le=100),
                     actor=Depends(get_current_user), svc=Depends(service)):
    offset = decode_cursor(cursor_value, actor.id)
    public = str(getattr(actor.role, "value", actor.role)) != "admin"
    # Search is applied before paging by the graph owner.
    if search:
        rows = await svc.characters.search_companion_candidates(ontology_id, search, offset, limit + 1, public_only=public)
    else:
        rows = await svc.characters.list_agents(ontology_id, "active", None, offset, limit + 1, public_only=public)
    return {"items": [{**{k: getattr(row, k, None) for k in ("id", "name", "subtitle", "image_url", "ontology_id", "status", "visibility")},
                       "identity_ready": bool(row.identity_description), "selectable": True} for row in rows[:limit]],
            "next_cursor": cursor(actor.id, offset + limit) if len(rows) > limit else None}


@router.get("/selection", response_model=SelectionRead)
async def selection(actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.selection(actor)


@router.put("/selection", response_model=SelectionRead)
async def choose(payload: SelectionWrite, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.select(actor, payload.character_agent_id, payload.expected_version)


@router.delete("/selection", response_model=SelectionRead)
async def clear(expected_version: int = Query(..., ge=0), actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.select(actor, None, expected_version)


@router.get("/relationships", response_model=Page[RapportRead])
async def relationships(cursor_value: str | None = Query(None, alias="cursor"), limit: int = Query(20, ge=1, le=100), actor=Depends(get_current_user), svc=Depends(service)):
    offset = decode_cursor(cursor_value, actor.id)
    rows = await svc.repo.page(CompanionRapport, actor.id, offset=offset, limit=limit)
    return {"items": [await svc.repo.scores(row) for row in rows[:limit]], "next_cursor": cursor(actor.id, offset + limit) if len(rows) > limit else None}


@router.get("/relationships/{character_id}/rapport", response_model=RapportRead)
async def rapport(character_id: str, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.rapport(actor, character_id)


@router.get("/relationships/{character_id}/rapport/history", response_model=Page[RapportAuditRead])
async def rapport_history(character_id: str, cursor_value: str | None = Query(None, alias="cursor"), limit: int = Query(20, ge=1, le=100), actor=Depends(get_current_user), svc=Depends(service)):
    await svc.rapport(actor, character_id)
    offset = decode_cursor(cursor_value, actor.id)
    rows = await svc.repo.page(CompanionRapportAudit, actor.id, character_id=character_id, offset=offset, limit=limit)
    return {"items": [record(row) for row in rows[:limit]], "next_cursor": cursor(actor.id, offset + limit) if len(rows) > limit else None}


@router.post("/relationships/{character_id}/rapport/reset", response_model=RapportRead)
async def reset(character_id: str, payload: VersionWrite, key=Depends(idempotency_key), actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.reset(actor, character_id, payload, key)


@router.get("/chats", response_model=Page[ChatRead])
async def chats(character_agent_id: str | None = None, cursor_value: str | None = Query(None, alias="cursor"), limit: int = Query(20, ge=1, le=100), actor=Depends(get_current_user), svc=Depends(service)):
    offset = decode_cursor(cursor_value, actor.id)
    rows = await svc.repo.page(CompanionChat, actor.id, character_id=character_agent_id, offset=offset, limit=limit)
    return {"items": [record(row) for row in rows[:limit]], "next_cursor": cursor(actor.id, offset + limit) if len(rows) > limit else None}


@router.post("/chats", status_code=201, response_model=ChatRead)
async def create_chat(payload: ChatCreate, key=Depends(idempotency_key), actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.create_chat(actor, payload, key)


@router.get("/chats/{chat_id}", response_model=ChatRead)
async def chat(chat_id: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    return record(await svc.chat(actor, str(chat_id)))


@router.patch("/chats/{chat_id}", response_model=ChatRead)
async def rename(chat_id: UUID, payload: ChatUpdate, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.update_chat(actor, str(chat_id), payload)


@router.delete("/chats/{chat_id}", status_code=202)
async def delete_chat(chat_id: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.delete_chat(actor, str(chat_id))


@router.get("/chats/{chat_id}/messages", response_model=Page[MessageRead])
async def messages(chat_id: UUID, before: str | None = None, limit: int = Query(20, ge=1, le=100), actor=Depends(get_current_user), svc=Depends(service)):
    await svc.chat(actor, str(chat_id))
    offset = decode_cursor(before, actor.id)
    rows = list((await svc.session.execute(select(CompanionMessage).where(CompanionMessage.chat_id == str(chat_id)).order_by(
        CompanionMessage.created_at.desc(), CompanionMessage.id.desc()).offset(offset).limit(limit + 1))).scalars())
    return {"items": [record(row) for row in reversed(rows[:limit])], "next_cursor": cursor(actor.id, offset + limit) if len(rows) > limit else None}


@router.post("/chats/{chat_id}/turns", status_code=202, response_model=TurnQueued)
async def turn(chat_id: UUID, payload: TurnCreate, response: Response, background_tasks: BackgroundTasks, key=Depends(idempotency_key), actor=Depends(get_current_user), token=Depends(oauth2_scheme), svc=Depends(service)):
    claims = decode_access_token(token)
    result = await svc.admit(actor, str(chat_id), payload, key, {"user_id": actor.id, "sid": claims.get("sid"), "expires_at": claims["exp"]})
    response.headers["Location"] = result["result_url"]
    from app.services.companion_dispatch_service import dispatch_after_admission
    background_tasks.add_task(dispatch_after_admission)
    return result


@router.get("/turns/{turn_id}", response_model=TurnRead)
async def result(turn_id: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.snapshot(actor, str(turn_id))


@router.post("/turns/{turn_id}/cancel")
async def cancel(turn_id: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    return await svc.cancel(actor, str(turn_id))


async def event_response(request, actor, token, svc, after, last_id, turn_id=None):
    owner = actor.id
    if after and last_id and after != last_id:
        fail(422, "invalid_cursor")
    sequence = decode_cursor(after or last_id, owner)
    if turn_id:
        await svc.snapshot(actor, turn_id)
    if sequence:
        row = await svc.session.get(CompanionEvent, sequence)
        if not row or row.user_id != owner:
            fail(410, "event_cursor_expired")
    async def frames():
        nonlocal sequence
        last_auth = 0
        while not await request.is_disconnected():
            if time.monotonic() - last_auth > 15:
                try:
                    await get_current_user(token=token, session=svc.session)
                except Exception:
                    return
                last_auth = time.monotonic()
            rows = await svc.repo.events(owner, sequence, turn_id)
            frames = []
            terminal = False
            for row in rows:
                sequence = row.id
                identifier = cursor(owner, sequence)
                data = {"version": 1, "event_id": identifier, "occurred_at": utc_iso(row.created_at), "chat_id": row.chat_id, "turn_id": row.turn_id, "payload": row.payload}
                frames.append(f"id: {identifier}\nevent: {row.type}\ndata: {json.dumps(data)}\n\n")
                terminal |= bool(turn_id and row.type in {"turn.completed", "turn.failed", "turn.cancelled"})
            # Release read transactions between polls and before network backpressure.
            await svc.session.rollback()
            for frame in frames:
                yield frame
            if terminal:
                return
            if not rows:
                if turn_id:
                    current = await svc.snapshot(actor, turn_id)
                    await svc.session.rollback()
                    if current["status"] in TERMINAL:
                        # All retained events may already have been consumed or expired.
                        # The client recovers the final from the snapshot endpoint.
                        return
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.3 if rows else 1)
    return StreamingResponse(frames(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/events", responses={200:{"content":{"text/event-stream":{}}}})
async def events(request: Request, after: str | None = None, last_id: str | None = Header(None, alias="Last-Event-ID"), actor=Depends(get_current_user), token=Depends(oauth2_scheme), svc=Depends(service)):
    return await event_response(request, actor, token, svc, after, last_id)


@router.get("/turns/{turn_id}/events", responses={200:{"content":{"text/event-stream":{}}}})
async def turn_events(turn_id: UUID, request: Request, after: str | None = None, last_id: str | None = Header(None, alias="Last-Event-ID"), actor=Depends(get_current_user), token=Depends(oauth2_scheme), svc=Depends(service)):
    return await event_response(request, actor, token, svc, after, last_id, str(turn_id))


@router.post("/attachments", status_code=201, response_model=AttachmentRead)
async def upload(file: UploadFile = File(...), actor=Depends(get_current_user), svc=Depends(service)):
    return await CompanionAttachmentService(svc.session).upload(actor.id, file)


@router.get("/attachments/{identifier}", response_model=AttachmentRead)
async def attachment(identifier: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    attachments = CompanionAttachmentService(svc.session)
    return attachments.metadata(await attachments.get(actor.id, str(identifier)))


@router.get("/attachments/{identifier}/content")
async def image(identifier: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    row = await CompanionAttachmentService(svc.session).get(actor.id, str(identifier))
    return FileResponse(private_root() / row.storage_key, media_type="image/png", filename="attachment.png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/attachments/{identifier}", status_code=202)
async def delete_attachment(identifier: UUID, actor=Depends(get_current_user), svc=Depends(service)):
    return await CompanionAttachmentService(svc.session).delete(actor.id, str(identifier))


retrieval_router = APIRouter(prefix="/internal/companion", tags=["companion-internal"])


@retrieval_router.get("/authorize")
async def authorize_retrieval(tool: str, token=Depends(oauth2_scheme), session=Depends(get_db_session)):
    from app.services.companion_authorization import authorize
    return await authorize(session, token, tool)
