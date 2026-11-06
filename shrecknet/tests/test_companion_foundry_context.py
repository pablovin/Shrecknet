"""Foundry context admission, provenance, strict bounds and table isolation."""
import copy
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.models.character_companion import CompanionMessage, CompanionTurn
from app.models.ontology import Ontology
from app.models.user import User
from app.models.world import World
from app.schemas.character_companion import ChatCreate, FoundryContext, TurnCreate
from app.services.character_companion_service import CharacterCompanionService
from app.services.companion_foundry_context import foundry_evidence, resolve_foundry_scope
from app.jobs.companion.contracts import Answer


def snapshot():
    return {"version": 1, "captured_at": "2026-11-06T12:00:00Z", "table": {
        "foundry_world_id": "campaign-table", "title": "The Night Watch",
        "description": "A city campaign, played with house rules.", "foundry_version": "14",
        "system_id": "custom-system", "system_title": "Our RPG", "system_version": "1",
        "shrecknet_world_id": "city-world", "shrecknet_world_name": "Outdated name",
    }, "documents": [{"uuid": "Actor.hero", "kind": "Actor", "name": "Bao",
        "captured_at": "2026-11-06T12:00:00Z", "source_revision": "12", "parent_uuid": None,
        "coverage": "partial", "text": "A defender of the city.",
        "fields": [{"label": "Power Points", "value": 8}], "links": []}]}


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(version=2),
    lambda s: s.update(version=True),
    lambda s: s.update(captured_at="2026-11-06T12:00:00"),
    lambda s: s.update(private_flags={"gm": True}),
    lambda s: s["documents"].append(copy.deepcopy(s["documents"][0])),
    lambda s: s["documents"][0].update(uuid="javascript:alert(1)"),
    lambda s: s["documents"][0].update(kind="Scene"),
    lambda s: s["documents"][0].update(foundry_world_id="another-table"),
    lambda s: s["documents"][0].update(text="x" * 8001),
    lambda s: s["table"].update(description="x" * 4001),
    lambda s: s["documents"][0]["fields"][0].update(value={"secret": "raw JSON"}),
    lambda s: s["documents"][0]["fields"][0].update(value=float("nan")),
])
def test_strict_snapshot_contract(mutation):
    value = snapshot()
    mutation(value)
    with pytest.raises(ValidationError):
        FoundryContext.model_validate(value)


def test_byte_limit_covers_unicode_and_scalar_fields():
    value = snapshot()
    value["documents"][0]["fields"] = [{"label": str(i), "value": "é" * 500} for i in range(100)]
    with pytest.raises(ValidationError, match="64 KiB"):
        FoundryContext.model_validate(value)


def test_provenance_and_citations_do_not_promote_client_claims():
    value = FoundryContext.model_validate(snapshot()).model_dump(mode="json")
    rows = foundry_evidence(value)
    assert len(rows) == 2
    assert {row["epistemic_category"] for row in rows} == {"user_provided"}
    assert rows[1]["reference"]["uuid"] == "Actor.hero"
    assert rows[1]["structured_evidence"]["fields"][0]["value"] == 8
    assert rows[1]["evidence_id"] == foundry_evidence(value)[1]["evidence_id"]
    answer = Answer.model_validate({"segments": [{"id": "s1", "kind": "fact", "text": "The snapshot shows 8 Power Points.", "evidence_ids": [rows[1]["evidence_id"]]}], "coverage": "complete", "warnings": []})
    result = answer.final(rows, [])
    assert result["coverage"] == "partial"
    assert result["citations"][0]["source_type"] == "foundry"
    assert result["warnings"]
    assert TurnCreate(text="Hi", selection_version=1).foundry_context is None
    with pytest.raises(ValidationError):
        TurnCreate(text="", selection_version=1, foundry_context=value)


class Characters:
    async def ensure_queryable(self, *args, **kwargs):
        pass

    async def get_agent(self, identifier, **kwargs):
        return SimpleNamespace(id=identifier, name="Companion", ontology_id=99, status="active", identity_description={"identity_summary": "ready"})


@pytest.mark.asyncio
async def test_admission_freezes_snapshot_preserves_identity_and_replays(session_maker, monkeypatch):
    from app.services import character_companion_service as module
    settings = SimpleNamespace(companion_enabled=True, enable_ai_agents=True,
        model_companion_synthesis=SimpleNamespace(name="model"),
        model_companion_rapport_update=SimpleNamespace(name="model"),
        companion_web_enabled=False, companion_max_evidence_tokens=10000)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    async with session_maker() as session:
        user = User(username="context-owner", email="context@example.test", role="player")
        other = User(username="context-other", email="other-context@example.test", role="player")
        session.add_all([user, other, World(id="city-world", name="Canonical City")])
        await session.flush()
        session.add(Ontology(id=7, world_id="city-world", name="City lore"))
        await session.flush()
        svc = CharacterCompanionService(session, Characters())
        await svc.select(user, "companion", 0)
        chat = await svc.create_chat(user, ChatCreate(character_agent_id="companion", selection_version=1), str(uuid4()))
        payload = TurnCreate(text="Explain my abilities", selection_version=1, foundry_context=snapshot())
        principal = {"expires_at": int(time.time()) + 60, "sid": None}
        key = str(uuid4())
        admitted = await svc.admit(user, chat["id"], payload, key, principal)
        assert await svc.admit(user, chat["id"], payload, key, principal) == admitted
        turn = await session.get(CompanionTurn, admitted["turn_id"])
        assert turn.request["context"]["ontology_id"] == 7
        assert turn.request["context"]["character_ontology_id"] == 99
        assert turn.character_agent_id == "companion"
        assert turn.request["foundry_context"]["table"]["shrecknet_world_name"] == "Canonical City"
        message = (await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id == turn.id))).scalar_one()
        assert len(message.manifest) == 2
        assert message.manifest[1]["source_type"] == "foundry"
        changed = payload.model_copy(update={"text": "Changed question"})
        with pytest.raises(HTTPException) as error:
            await svc.admit(user, chat["id"], changed, key, principal)
        assert error.value.detail["code"] == "idempotency_conflict"
        with pytest.raises(HTTPException) as error:
            await svc.snapshot(other, turn.id)
        assert error.value.status_code == 404
        settings.companion_max_evidence_tokens = 1
        await svc.cancel(user, turn.id)
        with pytest.raises(HTTPException) as error:
            await svc.admit(user, chat["id"], payload, str(uuid4()), principal)
        assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_ambiguous_world_never_falls_back_to_companion_ontology(session_maker):
    async with session_maker() as session:
        session.add(World(id="city-world", name="City"))
        await session.flush()
        session.add_all([Ontology(id=1, name="One", world_id="city-world"), Ontology(id=2, name="Two", world_id="city-world")])
        await session.flush()
        value = snapshot()
        scope = await resolve_foundry_scope(session, value, {"surface": "global"})
        assert scope["ontology_id"] is None
        assert scope["chronicle_ontology_id"] is None
        assert (await resolve_foundry_scope(session, value, {"ontology_id": 2}))["ontology_id"] == 2
        with pytest.raises(HTTPException) as error:
            await resolve_foundry_scope(session, value, {"ontology_id": 99})
        assert error.value.detail["code"] == "context_scope_mismatch"
        value["table"] = None
        assert (await resolve_foundry_scope(session, value, {}))["ontology_id"] is None
        with pytest.raises(HTTPException):
            await resolve_foundry_scope(session, value, {"ontology_id": 2})


@pytest.mark.asyncio
async def test_router_does_not_search_other_campaigns_when_foundry_scope_missing(monkeypatch):
    from app.jobs.companion import runner
    monkeypatch.setattr(runner, "get_settings", lambda: SimpleNamespace(companion_web_enabled=False,
        companion_model_routing_enabled=False, model_companion_synthesis=None))
    turn = SimpleNamespace(request={"text": "Who lives in this world? Read session notes.",
        "context": {"ontology_id": None}, "foundry_context": snapshot(),
        "tools": {"allowed": None, "web": "disabled"}})
    assert await runner.route(turn, None, []) == []


@pytest.mark.asyncio
async def test_pre_upgrade_text_action_hash_still_replays(session_maker):
    async with session_maker() as session:
        user = User(username="legacy-replay", email="legacy-replay@example.test", role="player")
        session.add(user)
        await session.flush()
        svc = CharacterCompanionService(session, Characters())
        payload = TurnCreate(text="Hello", selection_version=1)
        legacy_body = payload.model_dump(mode="json")
        legacy_body.pop("foundry_context")
        chat_id, key = str(uuid4()), str(uuid4())
        _, digest = await svc.repo.replay(user.id, f"turn:{chat_id}", key, legacy_body)
        result = {"turn_id": str(uuid4()), "chat_id": chat_id, "status": "queued"}
        svc.repo.remember(user.id, f"turn:{chat_id}", key, digest, result)
        await session.commit()
        assert await svc.admit(user, chat_id, payload, key, {}) == result


@pytest.mark.asyncio
async def test_snapshot_reaches_synthesis_and_committed_history(session_maker, monkeypatch):
    from contextlib import asynccontextmanager
    from app.core.config_store import get_settings
    from app.jobs.companion import runner
    from app.services import character_companion_service as service_module
    settings = get_settings().model_copy(update={"companion_enabled": True, "enable_ai_agents": True,
        "companion_model_routing_enabled": False, "companion_max_evidence_tokens": 10000})
    monkeypatch.setattr(runner, "get_settings", lambda: settings)
    monkeypatch.setattr(service_module, "get_settings", lambda: settings)

    @asynccontextmanager
    async def database():
        async with session_maker() as session:
            yield session
    monkeypatch.setattr(runner, "database", database)
    captured = []

    async def load_identity(turn):
        return SimpleNamespace(id=turn.user_id, role="player"), {"character_agent": {"identity_description": {"identity_summary": "A helpful companion"}}}
    monkeypatch.setattr(runner, "load_identity", load_identity)

    class LLM:
        def __init__(self, **kwargs):
            pass

        async def stream_chat(self, *, messages, **kwargs):
            context = json.loads(messages[1]["content"])
            captured.append(context)
            source = next(row for row in context["evidence"] if row["reference"].get("uuid") == "Actor.hero")
            yield json.dumps({"segments": [{"id": "s1", "kind": "fact", "text": "The attached snapshot shows 8 Power Points.", "evidence_ids": [source["evidence_id"]]}], "coverage": "partial", "warnings": []})

        async def aclose(self):
            pass
    monkeypatch.setattr(runner, "ShreckLLMClient", LLM)
    async with session_maker() as session:
        user = User(username="pipeline-context", email="pipeline-context@example.test", role="player")
        session.add_all([user, World(id="city-world", name="The City")])
        await session.flush()
        svc = CharacterCompanionService(session, Characters())
        await svc.select(user, "companion", 0)
        chat = await svc.create_chat(user, ChatCreate(character_agent_id="companion", selection_version=1), str(uuid4()))
        payload = TurnCreate(text="Explain the attachment", selection_version=1, foundry_context=snapshot(), tools={"allowed": []})
        admitted = await svc.admit(user, chat["id"], payload, str(uuid4()), {"expires_at": int(time.time()) + 60, "sid": None})
        row = await session.get(CompanionTurn, admitted["turn_id"])
        row.lease = "test-lease"
        await session.commit()
        turn = SimpleNamespace(**{column.name: getattr(row, column.name) for column in row.__table__.columns})
    await runner.execute_turn(turn, "test-lease")
    assert len(captured) == 1
    assert captured[0]["evidence"][1]["structured_evidence"]["fields"][0]["value"] == 8
    assert "house rules" in captured[0]["evidence"][0]["excerpt"]
    async with session_maker() as session:
        row = await session.get(CompanionTurn, turn.id)
        assert row.status == "completed"
        messages = list((await session.execute(select(CompanionMessage).where(CompanionMessage.turn_id == row.id))).scalars())
        assert len(messages) == 2 and all(message.committed for message in messages)
        assert all(message.manifest[0]["source_type"] == "foundry" for message in messages)
        assert row.result["citations"][0]["reference"]["uuid"] == "Actor.hero"
