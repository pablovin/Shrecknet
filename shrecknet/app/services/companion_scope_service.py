"""Validated conversational ontology selection; character identity stays separate.

Ontology discovery follows ElderScopeService's authenticated-viewer policy. Scope
updates are fenced by the active turn lease and chat revision, before any grant.
"""
from sqlalchemy import select, update
from app.models.ontology import Ontology
from app.models.character_companion import CompanionChat, CompanionTurn, utcnow
from app.repositories.character_companion_repository import CompanionRepository, fail
from app.schemas.character_companion import TERMINAL


def ontology_record(row):
    return {'id': row.id, 'name': row.name, 'description': (row.description or '')[:500], 'rpg_system': row.rpg_system, 'revision': str(row.updated_at) if row.updated_at else None}


def scoped_context(context, ontology, *, changed=False):
    result = dict(context)
    if changed:
        for key in ('world_id', 'entity_instance_id', 'source_id', 'scene_id', 'shreckworld_id', 'library_item_ids',
                    'chronicle_id', 'chronicle_revision', 'section_id', 'selection_text', 'chronicle_ontology_id'):
            result.pop(key, None)
        result['surface'] = 'global'
    result['ontology_id'] = ontology['id']
    result['rpg_system'] = ontology.get('rpg_system')
    return result


class CompanionScopeService:
    def __init__(self, session):
        self.session = session
        self.repo = CompanionRepository(session)

    async def candidates(self, name=None):
        statement = select(Ontology).order_by(Ontology.name, Ontology.id)
        if name:
            statement = statement.where(Ontology.name.ilike('%' + name.replace('%', '').replace('_', '')[:255] + '%'))
        rows = (await self.session.execute(statement.limit(51))).scalars().all()
        return [ontology_record(row) for row in rows[:50]]

    async def exact(self, name):
        rows = (await self.session.execute(select(Ontology).where(Ontology.name.ilike(name.strip()[:255].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_'), escape='\\')).limit(51))).scalars().all()
        return [ontology_record(row) for row in rows]

    async def get(self, identifier):
        row = await self.session.get(Ontology, identifier)
        if not row:
            fail(422, 'ontology_unavailable')
        return ontology_record(row)

    async def state(self, turn):
        return await self.repo.owned(CompanionChat, turn.chat_id, turn.user_id)

    async def save(self, turn, lease, *, ontology=None, context=None, pending=None, override=None, event=None):
        # Acquire a conditional write before reading the chat revision. Cancellation
        # and stale workers cannot publish a selection or clarification.
        fence = await self.session.execute(update(CompanionTurn).where(
            CompanionTurn.id == turn.id, CompanionTurn.lease == lease,
            CompanionTurn.status.notin_(TERMINAL)
        ).values(updated_at=utcnow()))
        if fence.rowcount != 1:
            import asyncio
            raise asyncio.CancelledError()
        chat = await self.state(turn)
        values = {'pending_clarification': pending, 'updated_at': utcnow()}
        if ontology is not None:
            values['resolved_ontology'] = ontology
        if override is not None:
            values['scope_override'] = override
        if (ontology is not None and ontology != chat.resolved_ontology) or (override is not None and override != chat.scope_override):
            values['revision'] = chat.revision + 1
        changed = await self.session.execute(update(CompanionChat).where(
            CompanionChat.id == chat.id, CompanionChat.revision == chat.revision,
            CompanionChat.active_turn_id == turn.id, CompanionChat.deleted.is_(False)
        ).values(**values))
        if changed.rowcount != 1:
            fail(409, 'concurrent_scope_change')
        if context is not None:
            await self.session.execute(update(CompanionTurn).where(CompanionTurn.id == turn.id).values(execution_context=context))
        if event:
            await self.repo.event(turn.user_id, event[0], event[1], chat_id=turn.chat_id, turn_id=turn.id)
        await self.session.commit()
