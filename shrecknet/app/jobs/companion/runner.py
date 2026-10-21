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
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.integrations.companion.retrieval import CompanionRetrieval, web_available
from app.jobs.companion.prompts import ROUTER, SYNTHESIS, RAPPORT, IMAGE_INTERPRETATION, INTERIM
from app.jobs.companion.contracts import Answer, RapportProposal, provisional_text
from app.jobs.elder.context_budget import estimate_tokens
from app.models.character_companion import CompanionTurn, CompanionOutbox, CompanionChat, CompanionMessage, CompanionRapport, CompanionRapportAudit, CompanionAttachment, utcnow
from app.repositories.character_companion_repository import CompanionRepository, record, fail
from app.schemas.character_companion import DIMENSIONS, TERMINAL
from app.services.character_agent_service import CharacterAgentService
from app.services.companion_authorization import active_principal
from app.services.companion_attachment_service import private_root, attachment_evidence

logger = logging.getLogger(__name__)


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


def turn_target(target, turn):
    """Per-turn denial also fences optional routing, interim, and rapport searches."""
    if getattr(target, "web_search_enabled", False) and turn.request.get("tools", {}).get("web", "disabled") == "disabled":
        return target.model_copy(update={"web_search_enabled": False})
    return target


async def route(turn, llm, recent):
    s = get_settings()
    query = turn.request["text"].lower().strip(" !?.")
    context = turn.request.get("context") or {}
    allowed = turn.request["tools"]["allowed"]
    candidates = set(allowed if allowed is not None else ["atlas", "character_memory", "shreckworld", "chronicle", "web"])
    if turn.request["tools"]["web"] == "disabled":
        candidates.discard("web")
    if not web_available(s):
        candidates.discard("web")
    tools = set()
    if turn.request["tools"]["web"] != "require" and query in {"hi", "hello", "hey", "thanks", "thank you", "how are you", "good morning", "good night", "bonjour", "ciao", "olá"}:
        return []
    if any(word in query for word in ("remember", "your memory", "you feel", "you recall")):
        tools.add("character_memory")
    if context.get("shreckworld_id") or any(word in query for word in ("rule", "book", "mechanic", "dice")):
        tools.add("shreckworld")
    if context.get("chronicle_id") or any(word in query for word in ("chronicle", "session notes")):
        tools.add("chronicle")
    if any(word in query for word in ("record", "scene", "who ", "when ", "where ", "world", "happened")):
        tools.add("atlas")
    if turn.request["tools"]["web"] == "require" or ("web" in candidates and any(word in query for word in ("latest", "today", "internet", "search the web"))):
        tools.add("web")
    if not tools and s.companion_model_routing_enabled and s.model_companion_routing.name:
        try:
            raw = await asyncio.wait_for(llm.chat(model=turn_target(s.model_companion_routing, turn), messages=[{"role": "system", "content": ROUTER}, {"role": "user", "content": json.dumps({"query": query, "context": context, "recent": recent, "available_tools": sorted(candidates)})}], usage_tag="companion.routing"), s.companion_tool_timeout_s)
            parsed = json.loads(raw)
            proposed = parsed.get("tools", [])
            if not isinstance(proposed, list) or any(not isinstance(tool, str) for tool in proposed):
                raise ValueError("invalid_routing")
            tools = set(proposed) & candidates
        except Exception:
            # Optional routing cannot prevent an uncertainty-aware conversation.
            tools = set()
    return sorted(tools & candidates)


async def execute_turn(turn, lease):
    started = time.monotonic()
    settings = get_settings()
    metrics = {}
    if not settings.companion_enabled or not settings.enable_ai_agents:
        raise ValueError("companion_disabled")
    actor, identity = await load_identity(turn)
    metrics["identity_ms"] = round((time.monotonic()-started)*1000,2)
    await mutate(turn.id, lease, stage="routing", identity=identity)
    async with database() as session:
        repo = CompanionRepository(session)
        rapport = await repo.scores(await repo.pair(turn.user_id, turn.character_agent_id))
        messages = list((await session.execute(select(CompanionMessage).where(CompanionMessage.chat_id == turn.chat_id, CompanionMessage.committed.is_(True)).order_by(CompanionMessage.created_at.desc()).limit(12))).scalars())
        recent = [{"role": row.role, "content": row.content[:1300]} for row in reversed(messages)]
        previous_turn = (await session.execute(select(CompanionTurn).where(
            CompanionTurn.chat_id == turn.chat_id, CompanionTurn.status == "completed"
        ).order_by(CompanionTurn.created_at.desc()).limit(1))).scalars().first()
        previous = {"query": previous_turn.request.get("text", ""), "scope": previous_turn.request.get("context") or {},
                    "evidence": (previous_turn.result or {}).get("evidence", []), "created_at": previous_turn.created_at} if previous_turn else None
        images, image_sources = [], []
        for identifier in turn.request["attachment_ids"]:
            attachment = await repo.owned(CompanionAttachment, identifier, turn.user_id)
            if attachment.deleted:
                raise ValueError("attachment_deleted")
            image_sources.append(attachment_evidence(attachment, turn.chat_id))
            images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode((private_root()/attachment.storage_key).read_bytes()).decode()}})
    llm = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.companion_turn_timeout_s, max_retries=0)
    try:
        tools = await route(turn, llm, recent)
        if not tools and previous and turn.request["text"].casefold().strip().startswith(("what about ", "tell me more", "explain that", "why is that", "and then")):
            permitted = turn.request["tools"]["allowed"]
            tools = sorted({row["source_type"] for row in previous["evidence"]
                            if row["source_type"] in {"atlas","character_memory","shreckworld","chronicle","web"}
                            and (permitted is None or row["source_type"] in permitted)
                            and (row["source_type"] != "web" or (turn.request["tools"]["web"] != "disabled" and web_available(settings)))})
        retrieval_query = turn.request["text"]
        if images and tools:
            interpreted = ""
            async for part in llm.stream_chat(model=settings.model_companion_synthesis.model_copy(update={"web_search_enabled": False}),
                    messages=[{"role":"system","content":IMAGE_INTERPRETATION}, {"role":"user","content":[{"type":"text","text":json.dumps({"query":retrieval_query,"context":turn.request.get("context")})},*images]}],
                    max_tokens=1200, usage_tag="companion.image_interpretation"):
                interpreted += part
                if len(interpreted)>12000:
                    raise ValueError("image_interpretation_budget_exceeded")
            observations=json.loads(interpreted)
            if set(observations)!={"observations","uncertainties","retrieval_terms"} or any(not isinstance(values,list) or len(values)>10 or any(not isinstance(value,str) or len(value)>300 for value in values) for values in observations.values()):
                raise ValueError("invalid_image_interpretation")
            retrieval_query += " " + " ".join(observations["retrieval_terms"])
        async def interim():
            try:
                text=await llm.chat(model=turn_target(settings.model_companion_interim, turn),messages=[{"role":"system","content":INTERIM},{"role":"user","content":json.dumps({"identity_description":identity["character_agent"]["identity_description"],"query":turn.request["text"]})}],usage_tag="companion.interim",max_tokens=100)
                await mutate(turn.id,lease,event="companion.interim",payload={"text":text[:400],"provisional":True})
            except Exception:
                pass
        interim_task = asyncio.create_task(interim()) if tools and settings.companion_interim_enabled and settings.model_companion_interim.name else None

        await mutate(turn.id, lease, stage="retrieving", event="routing.status", payload={"status": "done", "tools": tools, "reason": "bounded_source_routing"})
        retrieval = CompanionRetrieval(turn, actor)
        results, evidence = [], image_sources
        for source in image_sources:
            await mutate(turn.id, lease, event="source.discovered", payload=source)
        async def one(tool):
            await mutate(turn.id, lease, event="retrieval.status", payload={"tool": tool, "status": "started"})
            cached = await retrieval.cached(tool, retrieval_query, previous)
            return cached if cached is not None else await retrieval.retrieve(tool, retrieval_query)
        retrieval_started=time.monotonic()
        tasks = [asyncio.create_task(one(tool)) for tool in tools]
        try:
            for task in asyncio.as_completed(tasks, timeout=settings.companion_retrieval_timeout_s):
                item = await task
                results.append(item)
                await mutate(turn.id, lease, event="retrieval.status", payload={key: item[key] for key in ("tool", "status")})
                for source in item["evidence"]:
                    if len(evidence) >= 20 or sum(estimate_tokens(json.dumps(e)) for e in evidence) + estimate_tokens(json.dumps(source)) > settings.companion_max_evidence_tokens:
                        break
                    evidence.append(source)
                    await mutate(turn.id, lease, event="source.discovered", payload=source)
        except asyncio.TimeoutError:
            completed = {r["tool"] for r in results}
            results.extend({"tool": tool, "status": "timed_out", "evidence": []} for tool in tools if tool not in completed)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if interim_task:
                interim_task.cancel()
                await asyncio.gather(interim_task, return_exceptions=True)
        metrics["retrieval_ms"]=round((time.monotonic()-retrieval_started)*1000,2)
        metrics["tool_results"]=[{key:row[key] for key in ("tool","status")} | {"cache_hit": row.get("cache_hit",False)} for row in results]
        context = {"identity_description": identity["character_agent"]["identity_description"], "personality_context": identity.get("personality_context"),
                   "rapport": rapport, "query": turn.request["text"], "context": turn.request.get("context"), "recent": recent, "evidence": evidence}
        serialized = json.dumps(context, ensure_ascii=False)
        if estimate_tokens(serialized) + estimate_tokens(SYNTHESIS) > settings.companion_max_evidence_tokens + 12000:
            raise ValueError("identity_context_budget_exceeded")
        await mutate(turn.id, lease, stage="synthesizing")
        content = [{"type": "text", "text": serialized}, *images] if images else serialized
        raw, delivered, pending = "", "", ""
        async for chunk in llm.stream_chat(model=settings.model_companion_synthesis.model_copy(update={"web_search_enabled": False}), messages=[{"role": "system", "content": SYNTHESIS}, {"role": "user", "content": content}], max_tokens=settings.companion_output_tokens):
            if not raw:
                metrics["synthesis_first_token_ms"]=round((time.monotonic()-started)*1000,2)
            raw += chunk
            if len(raw) > 160000:
                raise ValueError("output_budget_exceeded")
            projected = provisional_text(raw)
            if projected.startswith(delivered):
                pending = projected[len(delivered):]
                if len(pending) >= 40 or pending.endswith((".", "?", "!", "\n")):
                    await mutate(turn.id, lease, delta=pending, event="response.delta", payload={"segment_id": "draft", "delta": pending, "provisional": True})
                    delivered = projected
                    pending = ""
        if pending:
            await mutate(turn.id, lease, delta=pending, event="response.delta", payload={"segment_id": "draft", "delta": pending, "provisional": True})
        await mutate(turn.id, lease, stage="validating")
        result = Answer.model_validate_json(raw).final(evidence, results)
        result["model_targets"]={field:getattr(settings,field).model_dump() for field in COMPANION_MODEL_FIELDS}
        result["usage"]=getattr(llm,"last_response_metadata",{}).get("usage")
        result["cost"]=None
        # Identity and originating browser access can be revoked while retrieval runs.
        await load_identity(turn)
        await retrieval.validate_sources(result['citations'])
        async with database() as session:
            repo = CompanionRepository(session)
            current = await session.get(CompanionTurn, turn.id)
            metrics["execution_ms"]=round((time.monotonic()-started)*1000,2)
            current.metrics=metrics
            for identifier in turn.request["attachment_ids"]:
                attachment = await repo.owned(CompanionAttachment, identifier, turn.user_id)
                if attachment.deleted:
                    raise ValueError("attachment_deleted")
            await repo.terminal(current, "completed", result=result, lease=lease)
            await session.commit()
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
        messages = [record(row) for row in (await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id == turn.id))).scalars()]
    old_generation = turn.generation != generation
    # Purged conversations settle their sequence without reusing deleted content.
    deleted_exchange = not turn.identity or not messages
    proposal, error = None, None
    if not old_generation and not deleted_exchange:
        llm = ShreckLLMClient(base_url=settings.shreckllm_base_url, timeout=settings.companion_turn_timeout_s, max_retries=0)
        try:
            data = {"turn_id": turn.id, "generation": generation, "base_version": base_version, "completion_sequence": turn.completion_sequence,
                    "scores": scores, "messages": messages, **(turn.identity or {})}
            data["identity_description"] = data.pop("character_agent", {}).get("identity_description")
            raw = await llm.chat(model=turn_target(settings.model_companion_rapport_update, turn), messages=[{"role": "system", "content": RAPPORT}, {"role": "user", "content": json.dumps(data)}], usage_tag="companion.rapport_update", max_tokens=2000)
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
            await asyncio.wait_for(execute_turn(turn, lease), get_settings().companion_turn_timeout_s)
        elif kind == "rapport" and turn is not None:
            done = await evaluate_rapport(turn, lease)
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
                    allowed_rules = {"Missing evidence", "Unknown evidence ID", "Objective source cannot be a subjective memory", "Subjective memory cannot establish objective fact", "Duplicate segment IDs"}
                    if isinstance(exc, ValueError) and str(exc) in allowed_rules:
                        failure["validation_rule"] = str(exc)
                    current.metrics = {**(current.metrics or {}), **failure}
                    logger.warning("companion_turn_failed turn_id=%s stage=%s error_type=%s", aggregate, current.status, type(exc).__name__)
                    await CompanionRepository(session).terminal(current, "failed", error={"code": "companion_execution_failed", "retryable": True}, lease=lease)
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
