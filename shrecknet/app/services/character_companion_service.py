"""Companion selection, private chat lifecycle and durable admission rules."""
from __future__ import annotations
import time
from uuid import uuid4
from sqlalchemy import select, update
from app.core.config_store import get_settings, COMPANION_MODEL_FIELDS
from app.models.character_companion import CompanionChat, CompanionTurn, CompanionRapport, CompanionRapportAudit, CompanionAttachment, CompanionMessage, utcnow
from app.repositories.character_companion_repository import CompanionRepository, fail, record, utc_iso
from app.schemas.character_companion import DIMENSIONS, TERMINAL


class CharacterCompanionService:
    def __init__(self, session, characters):
        self.session, self.characters = session, characters
        self.repo = CompanionRepository(session)

    async def character(self, actor, character_id, *, queryable=False):
        public_only = str(getattr(actor.role, "value", actor.role)) != "admin"
        if queryable:
            await self.characters.ensure_queryable(character_id, public_only=public_only)
        return await self.characters.get_agent(character_id, public_only=public_only)

    async def selection(self, actor):
        row = await self.repo.selection(actor.id)
        result = {"character_agent_id": row.character_agent_id if row else None, "version": row.version if row else 0,
                  "availability": "none", "character": None, "selected_at": utc_iso(row.selected_at) if row and row.selected_at else None,
                  "updated_at": utc_iso(row.updated_at) if row else None}
        if row and row.character_agent_id:
            from fastapi import HTTPException
            try:
                agent = await self.character(actor, row.character_agent_id)
                result["character"] = {key: getattr(agent, key, None) for key in ("id", "name", "subtitle", "image_url", "ontology_id")}
                result["availability"] = "archived" if str(getattr(agent.status, "value", agent.status)) != "active" else "ready" if agent.identity_description else "identity_pending"
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                result["availability"] = "unavailable"
        return result

    async def select(self, actor, character_id, version):
        if character_id:
            await self.character(actor, character_id, queryable=True)
        row = await self.repo.set_selection(actor.id, character_id, version)
        if character_id:
            await self.repo.pair(actor.id, character_id, create=True)
        result = await self.selection(actor)
        await self.repo.event(actor.id, "selection.updated", result)
        await self.session.commit()
        return result

    async def require_selection(self, actor, character_id, version):
        selection = await self.repo.selection(actor.id)
        if not selection or not selection.character_agent_id:
            fail(409, "selection_required")
        if selection.version != version:
            fail(409, "selection_conflict")
        if selection.character_agent_id != character_id:
            fail(409, "chat_character_mismatch")
        return await self.character(actor, character_id, queryable=True)

    async def create_chat(self, actor, payload, key):
        body = payload.model_dump(mode="json")
        previous, digest = await self.repo.replay(actor.id, "chat", key, body)
        if previous:
            return previous
        await self.require_selection(actor, payload.character_agent_id, payload.selection_version)
        chat = CompanionChat(user_id=actor.id, character_agent_id=payload.character_agent_id,
                             title=payload.title or "New conversation", default_scope=body["default_scope"])
        self.session.add(chat)
        await self.session.flush()
        result = record(chat)
        self.repo.remember(actor.id, "chat", key, digest, result)
        await self.session.commit()
        return result

    async def chat(self, actor, chat_id):
        return await self.repo.owned(CompanionChat, chat_id, actor.id)

    async def update_chat(self, actor, chat_id, payload):
        chat = await self.chat(actor, chat_id)
        changed = await self.session.execute(update(CompanionChat).where(CompanionChat.id == chat.id, CompanionChat.revision == payload.expected_revision).values(
            title=payload.title, revision=payload.expected_revision + 1, updated_at=utcnow()))
        if changed.rowcount != 1:
            fail(409, "chat_conflict")
        await self.session.commit()
        await self.session.refresh(chat)
        return record(chat)

    async def delete_chat(self, actor, chat_id):
        chat = await self.repo.owned(CompanionChat, chat_id, actor.id, allow_deleted=True)
        if chat.deleted:
            return {"chat_id": chat_id, "status": "deleting"}
        if chat.active_turn_id:
            await self.cancel(actor, chat.active_turn_id, commit=False)
        chat.deleted = True
        from app.models.character_companion import CompanionOutbox
        self.session.add(CompanionOutbox(kind="purge", aggregate_id=chat.id))
        await self.session.commit()
        return {"chat_id": chat_id, "status": "deleting"}

    async def rapport(self, actor, character_id):
        pair = await self.repo.pair(actor.id, character_id)
        if pair is None:
            fail(404, "relationship_not_found")
        return await self.repo.scores(pair)

    async def reset(self, actor, character_id, payload, key):
        previous, digest = await self.repo.replay(actor.id, f"reset:{character_id}", key, payload.model_dump())
        if previous:
            return previous
        pair = await self.repo.pair(actor.id, character_id)
        if pair is None:
            fail(404, "relationship_not_found")
        before = await self.repo.scores(pair)
        changed = await self.session.execute(update(CompanionRapport).where(
            CompanionRapport.user_id == actor.id, CompanionRapport.character_agent_id == character_id,
            CompanionRapport.version == payload.expected_version,
        ).values(**DIMENSIONS, version=CompanionRapport.version + 1, generation=CompanionRapport.generation + 1, updated_at=utcnow()))
        if changed.rowcount != 1:
            fail(409, "rapport_conflict")
        await self.session.refresh(pair)
        result = await self.repo.scores(pair)
        self.session.add(CompanionRapportAudit(user_id=actor.id, character_agent_id=character_id, disposition="reset", data={"before": before, "after": result}))
        self.repo.remember(actor.id, f"reset:{character_id}", key, digest, result)
        await self.repo.event(actor.id, "rapport.reset", result)
        await self.session.commit()
        return result

    async def admit(self, actor, chat_id, payload, key, principal):
        settings = get_settings()
        body = payload.model_dump(mode="json")
        previous, digest = await self.repo.replay(actor.id, f"turn:{chat_id}", key, body)
        if previous:
            return previous
        if not settings.companion_enabled or not settings.enable_ai_agents:
            fail(403, "companion_disabled")
        if not settings.model_companion_synthesis.name or not settings.model_companion_rapport_update.name:
            fail(503, "model_unavailable")
        from app.repositories.auth_sessions import AuthSessionRepository
        if not await AuthSessionRepository(self.session).rate_limit(f"companion-turn:{actor.id}", int(time.time()), 60, 20, commit=False):
            fail(429, "turn_limit_exceeded")
        if payload.tools.web != "disabled" and not await AuthSessionRepository(self.session).rate_limit(f"companion-web:{actor.id}", int(time.time()), 60, 8, commit=False):
            fail(429, "web_rate_limited")
        chat = await self.chat(actor, chat_id)
        agent = await self.require_selection(actor, chat.character_agent_id, payload.selection_version)
        if not agent.identity_description:
            fail(409, "identity_unavailable")
        context = body["context"] if body["context"] is not None else chat.default_scope or {}
        # Canonical retrieval follows the authorized active page. Personal memory
        # remains scoped to the selected character even in a different world.
        explicit_ontology = context.get("ontology_id")
        if explicit_ontology:
            from app.models.ontology import Ontology
            ontology = await self.session.get(Ontology, explicit_ontology)
            if not ontology or (context.get("world_id") and ontology.world_id != context["world_id"]):
                fail(422, "context_scope_mismatch")
        context = {**context, "ontology_id": explicit_ontology or agent.ontology_id,
                   "chronicle_ontology_id": explicit_ontology, "character_ontology_id": agent.ontology_id}
        body["context"] = context
        if payload.tools.web == "require" and not (settings.companion_web_enabled and getattr(settings, "companion_web_search_url", None)):
            fail(503, "required_tool_unavailable")
        if payload.retry_of_turn_id:
            old = await self.repo.owned(CompanionTurn, str(payload.retry_of_turn_id), actor.id)
            if old.chat_id != chat.id or old.status not in {"failed", "cancelled", "interrupted"}:
                fail(409, "invalid_retry")
        pair = await self.repo.pair(actor.id, agent.id, create=True)
        turn = CompanionTurn(id=str(uuid4()), chat_id=chat.id, user_id=actor.id, character_agent_id=agent.id,
                             request=body, principal=principal, generation=pair.generation)
        total_size = 0
        image_sources = []
        if payload.attachment_ids and f"{settings.model_companion_synthesis.provider}/{settings.model_companion_synthesis.name}" not in settings.companion_image_models:
            fail(503, "image_model_unavailable")
        for identifier in payload.attachment_ids:
            attachment = await self.repo.owned(CompanionAttachment, str(identifier), actor.id)
            if attachment.deleted or attachment.turn_id:
                fail(409, "attachment_in_use")
            from datetime import timedelta
            if attachment.created_at.replace(tzinfo=utcnow().tzinfo) < utcnow() - timedelta(hours=24):
                fail(410, "attachment_expired")
            attachment.turn_id = turn.id
            from app.services.companion_attachment_service import attachment_evidence
            image_sources.append(attachment_evidence(attachment, chat.id))
            total_size += attachment.size
        if total_size > 20 * 1024 * 1024:
            fail(413, "turn_attachment_budget_exceeded")
        await self.repo.reserve(chat, turn)
        user_message = CompanionMessage(turn_id=turn.id, chat_id=chat.id, role="user", content=payload.text, committed=False, manifest=image_sources)
        self.session.add(user_message)
        await self.session.flush()
        result = {"turn_id": turn.id, "chat_id": chat.id, "status": "queued", "user_message_id": user_message.id,
                  "events_url": f"/users/me/character-companion/turns/{turn.id}/events", "result_url": f"/users/me/character-companion/turns/{turn.id}"}
        self.repo.remember(actor.id, f"turn:{chat_id}", key, digest, result)
        await self.session.commit()
        return result

    async def cancel(self, actor, turn_id, commit=True):
        turn = await self.repo.owned(CompanionTurn, turn_id, actor.id)
        await self.repo.terminal(turn, "cancelled")
        if commit:
            await self.session.commit()
        await self.session.refresh(turn)
        return {"turn_id": turn.id, "status": turn.status}

    async def snapshot(self, actor, turn_id):
        turn = await self.repo.owned(CompanionTurn, turn_id, actor.id)
        await self.chat(actor, turn.chat_id)
        from sqlalchemy import func
        from app.models.character_companion import CompanionEvent
        from app.repositories.character_companion_repository import cursor
        # Capture the projection and replay checkpoint in one SQL statement, even
        # on SQLite drivers whose read transactions use legacy autocommit mode.
        checkpoint = select(func.max(CompanionEvent.id)).where(CompanionEvent.user_id == actor.id).scalar_subquery()
        row = (await self.session.execute(select(CompanionTurn, checkpoint).where(
            CompanionTurn.id == turn.id, CompanionTurn.user_id == actor.id
        ).execution_options(populate_existing=True))).one()
        turn, sequence = row
        return {**record(turn, "principal", "identity", "lease"), "turn_id": turn.id, "last_event_id": cursor(actor.id, sequence or 0)}
