"""Durable turn execution. Outbox claims fence writes; network work holds no DB transaction."""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import logging
import time
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select, update
from app.core.config_store import get_settings, COMPANION_MODEL_FIELDS
from app.db.session import AsyncSessionCompat, get_sessionmaker
from app.graph.neo4j import get_driver
from app.integrations.llm.shreckllm_client import ShreckLLMClient, LLMProviderUnavailableError
from app.jobs.companion.prompts import RAPPORT
from app.jobs.companion.contracts import RapportProposal
from app.jobs.companion.errors import CompanionExecutionError
from app.models.character_companion import CompanionTurn, CompanionOutbox, CompanionChat, CompanionMessage, CompanionRapport, CompanionRapportAudit, CompanionAttachment, utcnow
from app.repositories.character_companion_repository import CompanionRepository, record, fail
from app.schemas.character_companion import DIMENSIONS, TERMINAL
from app.services.character_agent_service import CharacterAgentService
from app.services.companion_authorization import active_principal
from app.services.companion_attachment_service import private_root, attachment_evidence

logger = logging.getLogger(__name__)
OUTBOX_HEARTBEAT_INTERVAL_S = 15
OUTBOX_STALE_AFTER_S = 90


@asynccontextmanager
async def database():
    with get_sessionmaker()(expire_on_commit=False) as raw:
        yield AsyncSessionCompat(raw)


async def load_identity(turn):
    async with database() as session:
        actor = await active_principal(session, turn)
        public = getattr(actor.role, "value", actor.role) != "admin"
        user = SimpleNamespace(id=actor.id, role=actor.role)
    async with get_driver().session(database=get_settings().neo4j_database) as graph:
        snapshot = await CharacterAgentService(None, graph).load_query_snapshot(turn.character_agent_id, public_only=public, include_memories=False)
    if not snapshot["character_agent"]["identity_description"]:
        raise ValueError("identity_unavailable")
    return user, snapshot


async def mutate(turn_id, lease, *, stage=None, delta=None, event=None, payload=None, identity=None):
    async with database() as session:
        repo = CompanionRepository(session)
        # Write a conditional heartbeat before events; this fences cancellation and
        # expired workers even if their provider connection still emits tokens.
        values = {"updated_at": utcnow()}
        if stage:
            values["status"] = stage
        if event == "pipeline.status":
            values["stage"] = payload["stage"]
        if delta is not None:
            values["draft"] = CompanionTurn.draft + delta
        if identity is not None:
            values["identity"] = identity
        changed = await session.execute(update(CompanionTurn).where(
            CompanionTurn.id == turn_id, CompanionTurn.lease == lease, CompanionTurn.status.notin_(TERMINAL)).values(**values))
        if changed.rowcount != 1:
            raise asyncio.CancelledError()
        turn = await session.get(CompanionTurn, turn_id)
        if event:
            await repo.event(turn.user_id, event, payload or {}, chat_id=turn.chat_id, turn_id=turn_id)
        await session.commit()


def private_stage_target(target):
    """Keep provider-managed search confined to the dedicated web adapter."""
    if getattr(target, "web_search_enabled", False):
        return target.model_copy(update={"web_search_enabled": False})
    return target


async def execute_turn(turn, lease):
    started = time.monotonic()
    settings = get_settings()
    metrics = {}
    if not settings.companion_enabled or not settings.enable_ai_agents:
        raise ValueError("companion_disabled")
    actor, identity = await asyncio.wait_for(load_identity(turn), settings.companion_tool_timeout_s)
    metrics["identity_ms"] = round((time.monotonic()-started)*1000,2)
    await mutate(turn.id, lease, stage="routing", identity=identity)
    async with database() as session:
        repo = CompanionRepository(session)
        rapport = await repo.scores(await repo.pair(turn.user_id, turn.character_agent_id))
        recent = await repo.conversation_context(turn.chat_id, turn.user_id)
        images, image_sources = [], []
        for identifier in turn.request["attachment_ids"]:
            attachment = await repo.owned(CompanionAttachment, identifier, turn.user_id)
            if attachment.deleted:
                raise ValueError("attachment_deleted")
            image_sources.append(attachment_evidence(attachment, turn.chat_id))
            images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode((private_root()/attachment.storage_key).read_bytes()).decode()}})
    llm = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.companion_tool_timeout_s, max_retries=0)
    try:
        from app.jobs.companion.query_pipeline import run
        return await run(turn, lease, actor=actor, identity=identity, rapport=rapport,
            recent=recent, images=images, image_sources=image_sources, llm=llm,
            settings=settings, metrics=metrics, started=started)
    except Exception:
        # Retain safe stage/tool diagnostics even when generation/access checks fail.
        metrics['execution_ms'] = round((time.monotonic()-started)*1000, 2)
        metrics['llm_usage'] = llm.get_usage_summary() if hasattr(llm, 'get_usage_summary') else {}
        async with database() as session:
            await session.execute(update(CompanionTurn).where(CompanionTurn.id == turn.id,
                CompanionTurn.lease == lease, CompanionTurn.status.notin_(TERMINAL)).values(metrics=metrics))
            await session.commit()
        raise
    finally:
        await llm.aclose()


async def evaluate_rapport(turn, lease=None):
    settings = get_settings()
    async with database() as session:
        repo = CompanionRepository(session)
        pair = await repo.pair(turn.user_id, turn.character_agent_id)
        if pair.evaluated_sequence + 1 != turn.completion_sequence:
            return False  # A lower sequence must settle first, across all chats.
        base_version, generation = pair.version, pair.generation
        scores = {key: getattr(pair, key) for key in DIMENSIONS}
        messages = [record(row) for row in (await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id == turn.id, CompanionMessage.kind != "commentary"))).scalars()]
    old_generation = turn.generation != generation
    # Purged conversations settle their sequence without reusing deleted content.
    deleted_exchange = not turn.identity or not messages
    operational = (turn.result or {}).get("outcome") in {"needs_clarification", "ontology_updated"}
    proposal, error = None, None
    if not old_generation and not deleted_exchange and not operational:
        llm = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.companion_tool_timeout_s, max_retries=0)
        try:
            data = {"turn_id": turn.id, "generation": generation, "base_version": base_version, "completion_sequence": turn.completion_sequence,
                    "scores": scores, "messages": messages, **(turn.identity or {})}
            data["identity_description"] = data.pop("character_agent", {}).get("identity_description")
            raw = await asyncio.wait_for(llm.chat(model=private_stage_target(settings.model_companion_rapport_update), messages=[{"role": "system", "content": RAPPORT}, {"role": "user", "content": json.dumps(data)}], usage_tag="companion.rapport_update", max_tokens=2000), settings.companion_tool_timeout_s)
            proposal = RapportProposal.model_validate_json(raw)
        except Exception:
            error = "evaluation_failed"
        finally:
            await llm.aclose()
    async with database() as session:
        repo = CompanionRepository(session)
        outbox = (await session.execute(select(CompanionOutbox).where(CompanionOutbox.aggregate_id == turn.id, CompanionOutbox.kind == "rapport"))).scalars().first()
        if lease and (outbox is None or outbox.status != "running" or outbox.lease != lease):
            return False
        current_chat = await session.get(CompanionChat, turn.chat_id)
        if current_chat is None or current_chat.deleted:
            deleted_exchange, proposal, error = True, None, None
        if error and outbox.attempts < settings.companion_rapport_max_attempts:
            await repo.event(turn.user_id, "rapport.failed", {"character_agent_id": turn.character_agent_id, "turn_id": turn.id, "retrying": True, "code": error})
            await session.commit()
            return False
        after = proposal.apply(scores, messages) if proposal else scores
        changed = await session.execute(update(CompanionRapport).where(
            CompanionRapport.user_id == turn.user_id, CompanionRapport.character_agent_id == turn.character_agent_id,
            CompanionRapport.version == base_version, CompanionRapport.generation == generation,
            CompanionRapport.evaluated_sequence == turn.completion_sequence - 1,
        ).values(**after, version=base_version+1, evaluated_sequence=turn.completion_sequence,
                 interaction_count=CompanionRapport.interaction_count+1, last_turn_id=turn.id, updated_at=utcnow()))
        if changed.rowcount != 1:
            await session.rollback()
            return False
        disposition = "superseded_by_reset" if old_generation else "deleted_exchange" if deleted_exchange else "failed" if error else "applied" if after != scores else "no_change"
        session.add(CompanionRapportAudit(user_id=turn.user_id, character_agent_id=turn.character_agent_id, turn_id=turn.id, disposition=disposition,
                                         data={"before": scores, "after": after, "base_version": base_version, "generation": generation,
                                               "completion_sequence": turn.completion_sequence, "proposal": proposal.model_dump() if proposal else None}))
        await session.execute(update(CompanionTurn).where(CompanionTurn.id == turn.id).values(rapport_status=disposition))
        await repo.event(turn.user_id, "rapport.updated", {"character_agent_id": turn.character_agent_id, "turn_id": turn.id, "version": base_version+1,
            "generation": generation, "scores": after, "applied_deltas": {key: after[key]-scores[key] for key in DIMENSIONS}, "disposition": disposition}, turn_id=turn.id, chat_id=turn.chat_id)
        await session.commit()
    return True


async def heartbeat_outbox(identifier, lease, turn_id=None):
    """Keep long network operations leased without retaining a SQL transaction."""
    while True:
        await asyncio.sleep(OUTBOX_HEARTBEAT_INTERVAL_S)
        async with database() as session:
            if turn_id is not None:
                current = await session.get(CompanionTurn, turn_id)
                if current is None or current.lease != lease or current.status in TERMINAL:
                    return
            changed = await session.execute(update(CompanionOutbox).where(
                CompanionOutbox.id == identifier, CompanionOutbox.lease == lease,
                CompanionOutbox.status == 'running').values(updated_at=utcnow()))
            await session.commit()
            if changed.rowcount != 1:
                return


async def with_outbox_heartbeat(work, identifier, lease, turn_id=None):
    """Stop work promptly after lease loss/cancellation and clean up both tasks."""
    task = asyncio.create_task(work)
    heartbeat = asyncio.create_task(heartbeat_outbox(identifier, lease, turn_id))
    try:
        done, _ = await asyncio.wait({task, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        heartbeat.result()  # Propagate SQL failures, rather than running unleased.
        raise asyncio.CancelledError()
    finally:
        for pending in (task, heartbeat):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, heartbeat, return_exceptions=True)


async def process_outbox(identifier):
    lease = str(uuid4())
    async with database() as session:
        changed = await session.execute(update(CompanionOutbox).where(CompanionOutbox.id == identifier, CompanionOutbox.status == "pending").values(status="running", lease=lease, updated_at=utcnow(), attempts=CompanionOutbox.attempts+1))
        if changed.rowcount != 1:
            return
        outbox = await session.get(CompanionOutbox, identifier)
        kind, aggregate = outbox.kind, outbox.aggregate_id
        turn = await session.get(CompanionTurn, aggregate) if kind in {"turn","rapport"} else None
        if turn is not None:
            if kind == "turn":
                await session.execute(update(CompanionTurn).where(CompanionTurn.id == aggregate, CompanionTurn.status == "queued").values(lease=lease, updated_at=utcnow()))
            await session.refresh(turn)
            # Detach immutable task inputs from the transaction/session.
            turn = SimpleNamespace(**{column.name: getattr(turn, column.name) for column in turn.__table__.columns})
        await session.commit()
    done = True
    try:
        if kind == "turn" and turn is not None and turn.status not in TERMINAL:
            await asyncio.wait_for(with_outbox_heartbeat(execute_turn(turn, lease), identifier, lease, turn.id), get_settings().companion_turn_timeout_s)
        elif kind == "rapport" and turn is not None:
            done = await with_outbox_heartbeat(evaluate_rapport(turn, lease), identifier, lease)
        elif kind == "purge":
            await purge_chat(aggregate)
        elif kind == "attachment_delete":
            (private_root()/aggregate).unlink(missing_ok=True)
    except asyncio.CancelledError:
        pass  # Terminal CAS has already fenced a cancelled turn.
    except Exception as exc:
        if kind == "turn":
            async with database() as session:
                current = await session.get(CompanionTurn, aggregate)
                if current and current.lease == lease and current.status not in TERMINAL:
                    failure = {"failure_stage": current.status, "error_type": type(exc).__name__}
                    # Persist validation rule identifiers, never model output or
                    # exception inputs (which can contain private source text).
                    from pydantic import ValidationError
                    if isinstance(exc, ValidationError):
                        failure["validation_rules"] = [row["type"] for row in exc.errors(include_input=False, include_context=False)]
                    if type(exc) is ValueError and str(exc) == "empty_synthesis":
                        failure["validation_rule"] = "empty_synthesis"
                    current.metrics = {**(current.metrics or {}), **failure}
                    logger.warning("companion_turn_failed turn_id=%s stage=%s error_type=%s error_code=%s", aggregate, current.status, type(exc).__name__, exc.code if isinstance(exc, CompanionExecutionError) else "companion_execution_failed")
                    await CompanionRepository(session).terminal(current, "failed", error={"code": exc.code if isinstance(exc, CompanionExecutionError) else "llm_provider_unavailable" if isinstance(exc, LLMProviderUnavailableError) else "companion_execution_failed", "retryable": exc.retryable if isinstance(exc, CompanionExecutionError) else True}, lease=lease)
                    await session.commit()
        else:
            done = False
    async with database() as session:
        await session.execute(update(CompanionOutbox).where(CompanionOutbox.id == identifier, CompanionOutbox.lease == lease).values(status="done" if done else "pending", updated_at=utcnow()))
        await session.commit()


async def purge_chat(chat_id):
    from sqlalchemy import delete
    async with database() as session:
        turns = list((await session.execute(select(CompanionTurn.id).where(CompanionTurn.chat_id == chat_id))).scalars())
        attachments = list((await session.execute(select(CompanionAttachment).where(CompanionAttachment.turn_id.in_(turns)))).scalars())
        for attachment in attachments:
            (private_root()/attachment.storage_key).unlink(missing_ok=True)
            attachment.deleted = True
        from app.models.character_companion import CompanionEvent
        await session.execute(delete(CompanionEvent).where(CompanionEvent.chat_id == chat_id))
        await session.execute(delete(CompanionMessage).where(CompanionMessage.chat_id == chat_id))
        for audit in (await session.execute(select(CompanionRapportAudit).where(CompanionRapportAudit.turn_id.in_(turns)))).scalars():
            audit.data={key:value for key,value in audit.data.items() if key!='proposal'}
        await session.execute(update(CompanionTurn).where(CompanionTurn.chat_id == chat_id).values(draft="", result=None, identity=None, request={"text": "", "attachment_ids": []}))
        await session.commit()
