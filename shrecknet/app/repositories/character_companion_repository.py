"""SQL persistence and atomic ownership/CAS operations for companions."""
from __future__ import annotations
import base64
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, update, func
from fastapi import HTTPException
from app.models.character_companion import (
    CompanionSelection, CompanionRapport, CompanionChat, CompanionTurn, CompanionMessage,
    CompanionEvent, CompanionOutbox, CompanionRapportAudit, CompanionIdempotency, utcnow,
)
from app.schemas.character_companion import DIMENSIONS, TERMINAL


def fail(status, code):
    raise HTTPException(status, detail={"code": code, "message": code.replace("_", " "), "retryable": status in {409, 429, 503}})


def utc_iso(value: datetime) -> str:
    """SQLite drops tzinfo; Companion persistence timestamps are UTC instants."""
    return (value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def record(row, *exclude):
    return {c.name: (utc_iso(getattr(row, c.name)) if isinstance(getattr(row, c.name), datetime) else getattr(row, c.name))
            for c in row.__table__.columns if c.name not in exclude}


def cursor(user_id, sequence):
    return base64.urlsafe_b64encode(f"{user_id}:{sequence}".encode()).decode().rstrip("=")


def decode_cursor(value, user_id):
    if not value:
        return 0
    try:
        owner, seq = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode().split(":")
        if int(owner) != user_id:
            fail(404, "cursor_not_found")
        result = int(seq)
        if result < 0:
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        fail(422, "invalid_cursor")


class CompanionRepository:
    def __init__(self, session):
        self.session = session

    async def conversation_context(self, chat_id, user_id, *, max_exchanges=6, max_characters=48000):
        """Bounded recent complete exchanges; never slice an individual message."""
        turns = list((await self.session.execute(select(CompanionTurn.id).join(
            CompanionChat, CompanionChat.id == CompanionTurn.chat_id
        ).where(CompanionTurn.chat_id == chat_id, CompanionTurn.user_id == user_id,
                CompanionTurn.status == "completed", CompanionChat.deleted.is_(False)
        ).order_by(CompanionTurn.created_at.desc(), CompanionTurn.id.desc()).limit(max_exchanges))).scalars())
        rows = list((await self.session.execute(select(CompanionMessage).where(
            CompanionMessage.turn_id.in_(turns), CompanionMessage.committed.is_(True)
        ).order_by(CompanionMessage.role.desc()))).scalars())
        recent, used = [], 0
        for turn_id in turns:
            exchange = [{"role": row.role, "content": row.content} for row in rows if row.turn_id == turn_id]
            if [row["role"] for row in exchange] != ["user", "assistant"]:
                continue
            size = sum(len(row["content"]) for row in exchange)
            if used + size > max_characters:
                break
            recent = exchange + recent
            used += size
        return recent

    async def lock_chat_quota(self, user_id):
        # A real write serializes quota decisions on SQLite and PostgreSQL.
        # The user exists even before a Companion selection is established.
        from app.models.user import User
        await self.session.execute(update(User).where(User.id == user_id).values(username=User.username))

    async def chat_count(self, user_id):
        return (await self.session.execute(select(func.count()).select_from(CompanionChat).where(
            CompanionChat.user_id == user_id, CompanionChat.deleted.is_(False)))).scalar_one()

    async def owned(self, model, identifier, user_id, *, allow_deleted=False):
        row = (await self.session.execute(select(model).where(model.id == identifier, model.user_id == user_id))).scalars().first()
        if row is None or (isinstance(row, CompanionChat) and row.deleted and not allow_deleted):
            fail(404, "resource_not_found")
        return row

    async def selection(self, user_id):
        return await self.session.get(CompanionSelection, user_id)

    async def pair(self, user_id, character_id, create=False):
        row = await self.session.get(CompanionRapport, (user_id, character_id))
        if row is None and create:
            row = CompanionRapport(user_id=user_id, character_agent_id=character_id)
            self.session.add(row)
            await self.session.flush()
        return row

    async def event(self, user_id, event_type, payload, *, chat_id=None, turn_id=None):
        row = CompanionEvent(user_id=user_id, type=event_type, payload=payload, chat_id=chat_id, turn_id=turn_id)
        self.session.add(row)
        await self.session.flush()
        return row

    async def events(self, user_id, after=0, turn_id=None, limit=100):
        query = select(CompanionEvent).where(CompanionEvent.user_id == user_id, CompanionEvent.id > after,
            (CompanionEvent.chat_id.is_(None)) | CompanionEvent.chat_id.in_(
                select(CompanionChat.id).where(CompanionChat.user_id == user_id, CompanionChat.deleted.is_(False))))
        if turn_id:
            query = query.where(CompanionEvent.turn_id == turn_id)
        return list((await self.session.execute(query.order_by(CompanionEvent.id).limit(limit))).scalars())

    async def replay(self, user_id, operation, key, body):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = await self.session.get(CompanionIdempotency, (user_id, operation, key))
        if row:
            if row.body_hash != digest:
                fail(409, "idempotency_conflict")
            return row.result, digest
        return None, digest

    def remember(self, user_id, operation, key, digest, result):
        self.session.add(CompanionIdempotency(user_id=user_id, operation=operation, key=key, body_hash=digest, result=result))

    async def set_selection(self, user_id, character_id, expected):
        current = await self.selection(user_id)
        if current is None:
            if expected != 0:
                fail(409, "selection_conflict")
            current = CompanionSelection(user_id=user_id, version=0)
            self.session.add(current)
            await self.session.flush()
        if current.version != expected:
            fail(409, "selection_conflict")
        if current.character_agent_id == character_id:
            return current
        result = await self.session.execute(update(CompanionSelection).where(
            CompanionSelection.user_id == user_id, CompanionSelection.version == expected,
        ).values(character_agent_id=character_id, version=expected + 1,
                 selected_at=utcnow() if character_id else None, updated_at=utcnow()))
        if result.rowcount != 1:
            fail(409, "selection_conflict")
        await self.session.refresh(current)
        return current

    async def page(self, model, user_id, *, character_id=None, offset=0, limit=20):
        query = select(model).where(model.user_id == user_id)
        if hasattr(model, "deleted"):
            query = query.where(model.deleted.is_(False))
        if character_id:
            query = query.where(model.character_agent_id == character_id)
        ordering = model.updated_at if hasattr(model, "updated_at") else model.created_at
        query = query.order_by(ordering.desc(), model.character_agent_id if model is CompanionRapport else model.id)
        return list((await self.session.execute(query.offset(offset).limit(limit + 1))).scalars())

    async def reserve(self, chat, turn):
        # A short write to the user's singleton selection serializes admissions
        # across that user's chats on SQLite and PostgreSQL alike.
        await self.session.execute(update(CompanionSelection).where(CompanionSelection.user_id == turn.user_id).values(updated_at=CompanionSelection.updated_at))
        count = (await self.session.execute(select(func.count()).select_from(CompanionTurn).where(CompanionTurn.user_id == turn.user_id, CompanionTurn.status.notin_(TERMINAL)))).scalar()
        if count >= 2:
            fail(429, "turn_limit_exceeded")
        result = await self.session.execute(update(CompanionChat).where(
            CompanionChat.id == chat.id, CompanionChat.active_turn_id.is_(None), CompanionChat.deleted.is_(False),
        ).values(active_turn_id=turn.id, updated_at=utcnow()))
        if result.rowcount != 1:
            fail(409, "chat_busy")
        self.session.add(turn)
        await self.session.flush()
        self.session.add(CompanionOutbox(kind="turn", aggregate_id=turn.id))
        await self.event(turn.user_id, "turn.created", {"turn_id": turn.id, "status": "queued"}, chat_id=chat.id, turn_id=turn.id)

    async def terminal(self, turn, status, *, result=None, error=None, lease=None):
        conditions = [CompanionTurn.id == turn.id, CompanionTurn.status.notin_(TERMINAL)]
        if lease:
            conditions.append(CompanionTurn.lease == lease)
        changed = await self.session.execute(update(CompanionTurn).where(*conditions).values(
            status=status, result=result, error=error, updated_at=utcnow()))
        if changed.rowcount != 1:
            return False
        await self.session.execute(update(CompanionChat).where(
            CompanionChat.id == turn.chat_id, CompanionChat.active_turn_id == turn.id).values(active_turn_id=None))
        if status == "completed":
            pair = await self.pair(turn.user_id, turn.character_agent_id)
            # Updating the pair serializes completions across separate chats.
            await self.session.execute(update(CompanionRapport).where(
                CompanionRapport.user_id == turn.user_id, CompanionRapport.character_agent_id == turn.character_agent_id,
            ).values(next_sequence=CompanionRapport.next_sequence + 1))
            await self.session.refresh(pair)
            turn.completion_sequence = pair.next_sequence
            turn.rapport_status = "pending"
            message = CompanionMessage(turn_id=turn.id, chat_id=turn.chat_id, role="assistant", content=result["text"], manifest=result["citations"])
            self.session.add(message)
            await self.session.execute(update(CompanionMessage).where(CompanionMessage.turn_id == turn.id, CompanionMessage.role == "user").values(committed=True))
            await self.session.flush()
            await self.event(turn.user_id, "response.final", {**result, "message_id": message.id}, chat_id=turn.chat_id, turn_id=turn.id)
            self.session.add(CompanionOutbox(kind="rapport", aggregate_id=turn.id))
        event = "turn.completed" if status == "completed" else "turn.cancelled" if status == "cancelled" else "turn.failed"
        await self.event(turn.user_id, event, {"status": status, "rapport_status": "pending" if status == "completed" else "not_applicable", **(error or {})}, chat_id=turn.chat_id, turn_id=turn.id)
        return True

    async def scores(self, pair):
        pending = pair.next_sequence - pair.evaluated_sequence
        failed = (await self.session.execute(select(func.count()).select_from(CompanionRapportAudit).where(CompanionRapportAudit.user_id == pair.user_id, CompanionRapportAudit.character_agent_id == pair.character_agent_id, CompanionRapportAudit.disposition == "failed"))).scalar()
        return {"character_agent_id": pair.character_agent_id, "scores": {key: getattr(pair, key) for key in DIMENSIONS},
                "version": pair.version, "generation": pair.generation, "last_turn_id": pair.last_turn_id,
                "updated_at": utc_iso(pair.updated_at), "pending_count": pending, "failed_count": failed}

    async def delete_user_records(self, user_id):
        """Explicit cleanup also works on SQLite installations without FK pragmas."""
        from sqlalchemy import delete
        from app.models.character_companion import CompanionAttachment
        turn_ids=select(CompanionTurn.id).where(CompanionTurn.user_id==user_id)
        for key in (await self.session.execute(select(CompanionAttachment.storage_key).where(CompanionAttachment.user_id==user_id))).scalars():
            self.session.add(CompanionOutbox(kind="attachment_delete",aggregate_id=key))
        await self.session.execute(delete(CompanionMessage).where(CompanionMessage.turn_id.in_(turn_ids)))
        for model in (CompanionEvent,CompanionRapportAudit,CompanionAttachment,CompanionTurn,CompanionChat,CompanionRapport,CompanionSelection,CompanionIdempotency):
            await self.session.execute(delete(model).where(model.user_id==user_id))
