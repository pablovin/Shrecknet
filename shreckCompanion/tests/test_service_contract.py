from __future__ import annotations

import asyncio
import json
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from app.core.config import Settings
from app.schemas import (
    AllocatedToolAgent,
    CompanionChatSessionCreateRequest,
    CompanionChatSessionUpdateRequest,
    OrchestratorToolAllocation,
    PersonalCompanionAgentCreate,
)
from app.service import CompanionService


class FakeProviderClient:
    def __init__(self) -> None:
        self.elder_started = asyncio.Event()
        self.allow_elder_finish = asyncio.Event()
        self.elder_queries: list[str] = []
        self.librarian_queries: list[str] = []

    async def aclose(self) -> None:
        return None

    async def allocate_tools(
        self,
        *,
        user_id: int,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> OrchestratorToolAllocation:
        return OrchestratorToolAllocation(
            elder=[AllocatedToolAgent(id="elder-1", name="Elder", job="elder", ontology_ids=[ontology_id])],
            librarian=[AllocatedToolAgent(id="librarian-1", name="Librarian", job="librarian", ontology_ids=[ontology_id])],
        )

    async def run_elder(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        self.elder_queries.append(query)
        self.elder_started.set()
        await self.allow_elder_finish.wait()
        return {
            "ok": True,
            "agent_id": agent_id,
            "agent_name": "Elder",
            "agent_job": "elder",
            "answer": "Ernst von Einsenwald is a quiet office worker who stays secretive about the past.",
            "sources": [
                {
                    "node_id": "n1",
                    "node_label": "Character",
                    "node_name": "Ernst von Einsenwald",
                    "score": 0.9,
                    "node_type": "general",
                },
                {
                    "node_id": "scene-1",
                    "node_label": "Scene",
                    "node_name": "Berlin Office",
                    "score": 0.8,
                    "node_type": "scene",
                    "scene_id": "scene-1",
                    "source_entity_instance_id": "n1",
                    "evidence_chunks": [{"chunk_type": "scene_main", "text": "Ernst works quietly in a dust dulled office."}],
                },
            ],
        }

    async def run_librarian(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        self.librarian_queries.append(query)
        return {
            "ok": True,
            "agent_id": agent_id,
            "agent_name": "Librarian",
            "agent_job": "librarian",
            "answer": "Based on those traits, occupations like clerk or bureaucrat fit best.",
            "sources_used": [
                {
                    "library_item_id": 2,
                    "book_title": "CoC Investigator Rulebook",
                    "book_authors": "Chaosium",
                    "page_number": 114,
                    "page_url": "http://localhost:8100/media/library/1/2/content.pdf#page=114",
                    "pdf_url": "http://localhost:8100/media/library/1/2/content.pdf",
                    "score": 0.8,
                    "text": "Treatment by a psychotherapist can recover Sanity points.",
                }
            ],
        }


class FakeLLMClient:
    async def aclose(self) -> None:
        return None

    async def chat(self, **kwargs) -> str:
        if "planning" in str(kwargs.get("usage_tag") or ""):
            return '{"strategy":"sequential","reason":"Need canon context before rules mapping.","steps":[{"step_id":"step-1","tool_job":"elder","goal":"Find Ernst\\u2019s grounded role and traits.","query":"Who is Ernst von Einsenwald, and what grounded traits, role, and behavior are shown in canon?","depends_on":[],"use_prior_context":false,"success_requirements":["grounded_subject_context"],"on_failure":"stop"},{"step_id":"step-2","tool_job":"librarian","goal":"Map those grounded traits to plausible occupations.","query":"Based on the game rules, which occupations fit Ernst?","depends_on":["step-1"],"use_prior_context":true,"success_requirements":["rules_answer"],"on_failure":"stop"}]}'
        return "Ernst von Einsenwald is grounded as a quiet office worker, so clerk or bureaucrat are the best-supported occupations."


class GenericRulesLLMClient:
    async def aclose(self) -> None:
        return None

    async def chat(self, **kwargs) -> str:
        if "planning" in str(kwargs.get("usage_tag") or ""):
            return '{"needs_tools":true,"strategy":"parallel","reason":"generic_rules_librarian_only","steps":[{"step_id":"step-1","tool_job":"librarian","goal":"Determine the rules for Sanity recovery.","query":"What are the mechanics for recovering Sanity in the game system?","depends_on":[],"use_prior_context":false,"success_requirements":["Sanity recovery mechanics"],"on_failure":"stop"}]}'
        return "Recovering from indefinite insanity requires institutional care, and psychotherapy may form a part of that care."


class MultiBookProviderClient(FakeProviderClient):
    async def run_librarian(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        self.librarian_queries.append(query)
        return {
            "ok": True,
            "agent_id": agent_id,
            "agent_name": "Librarian",
            "agent_job": "librarian",
            "answer": "Sanity recovery is described in [CoC Investigator Rulebook, p.114](http://localhost:8100/media/library/99/2/content.pdf#page=114) and [Keeper Rulebook, p.167](http://localhost:8100/media/library/99/3/content.pdf#page=167).",
            "sources_used": [
                {
                    "library_item_id": 2,
                    "book_title": "CoC Investigator Rulebook",
                    "book_authors": "Chaosium",
                    "page_number": 114,
                    "page_url": "http://localhost:8100/media/library/99/2/content.pdf#page=114",
                    "pdf_url": "http://localhost:8100/media/library/99/2/content.pdf",
                    "score": 0.8,
                    "text": "Institutional care is required for indefinite insanity.",
                },
                {
                    "library_item_id": 3,
                    "book_title": "Keeper Rulebook",
                    "book_authors": "Chaosium",
                    "page_number": 167,
                    "page_url": "http://localhost:8100/media/library/99/3/content.pdf#page=167",
                    "pdf_url": "http://localhost:8100/media/library/99/3/content.pdf",
                    "score": 0.78,
                    "text": "Psychotherapy can help recover lost Sanity points over time.",
                },
            ],
        }


class MemoryAwareLLMClient:
    def __init__(self) -> None:
        self.planning_prompts: list[str] = []

    async def aclose(self) -> None:
        return None

    async def chat(self, **kwargs) -> str:
        usage_tag = str(kwargs.get("usage_tag") or "")
        prompt = str((kwargs.get("messages") or [{}])[0].get("content") or "")
        if "planning" in usage_tag:
            self.planning_prompts.append(prompt)
            if "Ernst" in prompt and "What would help Ernst?" in prompt:
                return '{"strategy":"sequential","reason":"Need canon context before equipment rules.","steps":[{"step_id":"step-1","tool_job":"elder","goal":"Recall Ernst context.","query":"Who is Ernst and what has he done so far?","depends_on":[],"use_prior_context":false,"success_requirements":["grounded_subject_context"],"on_failure":"stop"},{"step_id":"step-2","tool_job":"librarian","goal":"Suggest helpful equipment.","query":"What would help Ernst?","depends_on":["step-1"],"use_prior_context":true,"success_requirements":["rules_answer"],"on_failure":"stop"}]}'
            if "recent conversation" in prompt.lower():
                return '{"strategy":"parallel","reason":"Direct follow-up resolved from conversation memory.","steps":[{"step_id":"step-1","tool_job":"elder","goal":"Resolve the follow-up subject.","query":"What would help Ernst?","depends_on":[],"use_prior_context":false,"success_requirements":["direct_answer"],"on_failure":"stop"}]}'
            return '{"strategy":"parallel","reason":"Direct answer.","steps":[{"step_id":"step-1","tool_job":"elder","goal":"Answer directly.","query":"Who is Ernst?","depends_on":[],"use_prior_context":false,"success_requirements":["direct_answer"],"on_failure":"stop"}]}'
        return "Ernst benefits from prepared investigation gear. Sources: CoC Investigator Rulebook, p.114."


class PolicyDisablesKnowledgeLLMClient:
    def __init__(self) -> None:
        self.planning_prompts: list[str] = []

    async def aclose(self) -> None:
        return None

    async def chat(self, **kwargs) -> str:
        usage_tag = str(kwargs.get("usage_tag") or "")
        if "policy" in usage_tag:
            return (
                '{"chat_goal":"Answer quickly.","turn_intention":"Give a direct reply.",'
                '"conversation_mode":"general_assistant","user_need":"information",'
                '"needs_knowledge_tools":false,"suggested_response_style":'
                '{"directness":0.9,"technical_depth":0.1,"playfulness":0.0,"initiative":0.1},'
                '"open_threads":["Ernst"],"next_best_actions":["answer"]}'
            )
        if "planning" in usage_tag:
            prompt = str((kwargs.get("messages") or [{}])[0].get("content") or "")
            self.planning_prompts.append(prompt)
            return (
                '{"needs_tools":true,"strategy":"parallel","reason":"Need elder for named subject.","steps":['
                '{"step_id":"step-1","tool_job":"elder","goal":"Resolve named subject.",'
                '"query":"Is Ernst a human?","depends_on":[],"use_prior_context":false,'
                '"success_requirements":["direct_answer"],"on_failure":"stop"}]}'
            )
        return "Ernst is human based on grounded records."


async def _create_chat(service: CompanionService, *, user_id: int, ontology_id: int, title: str | None = None):
    return await service.create_chat_session(
        user_id=user_id,
        payload=CompanionChatSessionCreateRequest(ontology_id=ontology_id, title=title),
    )


@pytest.mark.asyncio
async def test_companion_service_frontend_contract(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = FakeProviderClient()
    service.provider_client = provider
    service.llm_client = FakeLLMClient()
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(
            name="Fiona",
            writing_style="Concise and grounded.",
            active=True,
        ),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)
    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="Who is Shrek and what rules apply?",
    )
    snapshot_path = tmp_path / "media" / "turn_jobs" / "12" / f"{queued.job_id}.json"
    queued_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert queued_snapshot["job_id"] == queued.job_id
    assert queued_snapshot["status"] == "queued"
    assert queued_snapshot["payload"]["status"] == "queued"
    assert queued_snapshot["payload"]["query"] == "Who is Shrek and what rules apply?"
    assert queued_snapshot["payload"]["session_id"] == chat.session_id
    assert queued_snapshot["payload"]["ontology_id"] == 99
    assert queued_snapshot["payload"]["companion_id"] == companion.id
    assert queued_snapshot["payload"]["allocated_tools"] == {
        "elder": [
            {
                "id": "elder-1",
                "name": "Elder",
                "job": "elder",
                "ontology_ids": [99],
            }
        ],
        "librarian": [
            {
                "id": "librarian-1",
                "name": "Librarian",
                "job": "librarian",
                "ontology_ids": [99],
            }
        ],
    }
    assert queued_snapshot["payload"]["conversation_context"]["resolved_subject"] == "Shrek"

    await asyncio.wait_for(provider.elder_started.wait(), timeout=1)
    running = service.store.get_turn_job(12, queued.job_id)
    assert running is not None
    assert running["status"] == "running"
    assert running["payload"]["phase"] == "executing_steps"
    assert running["payload"]["phase_label"] == "Executing tool plan"
    assert running["payload"]["progress"] == {"current": 4, "total": 6}
    assert "llm_trace" in running["payload"]
    assert "policy" in running["payload"]["llm_trace"]
    assert "planning" in running["payload"]["llm_trace"]
    assert "prompt" in running["payload"]["llm_trace"]["policy"]
    assert "response" in running["payload"]["llm_trace"]["planning"]
    assert running["payload"]["routing"] == {
        "use_elder": True,
        "use_librarian": True,
        "reason": "Need canon context before rules mapping.",
    }
    assert running["payload"]["selected_tools"] == {"elder": ["elder-1"], "librarian": ["librarian-1"]}
    assert running["payload"]["step_progress"] == {"total": 2, "completed": 0, "running": 1, "current": 1}
    assert running["payload"]["current_step"]["tool_job"] == "elder"
    running_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert running_snapshot == running

    provider.allow_elder_finish.set()
    result = None
    for _ in range(20):
        result = service.store.get_turn_job(12, queued.job_id)
        if result and result["status"] == "done":
            break
        await asyncio.sleep(0.05)

    assert companion.id == bootstrap.companion_id
    assert queued.status == "queued"
    assert result is not None
    assert result["status"] == "done"
    assert provider.elder_queries
    assert provider.librarian_queries
    assert "resolved_subject: Ernst von Einsenwald" in provider.librarian_queries[0]
    assert "grounded_roles" in provider.librarian_queries[0]
    assert result["payload"]["final"]["text"] == (
        "Ernst von Einsenwald is grounded as a quiet office worker, so clerk or bureaucrat are the best-supported occupations. "
        "Sources: CoC Investigator Rulebook, p.114."
    )
    assert result["payload"]["final"]["linked_text"].count("<a ") >= 1
    assert 'data-node-id="n1"' in result["payload"]["final"]["linked_text"]
    assert 'data-node-name="Ernst von Einsenwald"' in result["payload"]["final"]["linked_text"]
    assert result["payload"]["selected_tools"] == {"elder": ["elder-1"], "librarian": ["librarian-1"]}
    assert result["payload"]["plan"]["strategy"] == "sequential"
    assert len(result["payload"]["execution"]["completed_steps"]) == 2
    assert result["payload"]["execution"]["stopped_reason"] is None
    elder_sources = result["payload"]["agent_responses"][0]["sources"]
    assert elder_sources[0]["node_type"] == "general"
    assert elder_sources[1]["node_type"] == "scene"
    assert elder_sources[1]["source_entity_instance_id"] == "n1"
    rules_sources = result["payload"]["agent_responses"][1]["sources"]
    assert len(rules_sources) == 1
    assert rules_sources[0]["source_type"] == "book"
    assert rules_sources[0]["node_type"] == "general"
    assert rules_sources[0]["ontology_id"] == 99
    assert rules_sources[0]["library_item_id"] == 2
    assert rules_sources[0]["book_title"] == "CoC Investigator Rulebook"
    assert rules_sources[0]["book_authors"] == "Chaosium"
    assert rules_sources[0]["page_number"] == 114
    assert rules_sources[0]["page_url"] == "http://localhost:8100/media/library/1/2/content.pdf#page=114"
    assert rules_sources[0]["pdf_url"] == "http://localhost:8100/media/library/1/2/content.pdf"
    assert rules_sources[0]["text"] == "Treatment by a psychotherapist can recover Sanity points."
    assert result["payload"]["final"]["references"]["inline_links"][0]["node_name"] == "Ernst von Einsenwald"
    assert result["payload"]["final"]["references"]["timeline_sources"][0]["node_id"] == "scene-1"
    assert result["payload"]["final"]["references"]["timeline_sources"][0]["source_entity_instance_id"] == "n1"
    assert result["payload"]["final"]["references"]["timeline_sources"][0]["source_entity"]["node_name"] == "Ernst von Einsenwald"
    assert result["payload"]["final"]["references"]["book_sources"][0]["library_item_id"] == 2
    assert result["payload"]["final"]["references"]["book_sources"][0]["pages"] == [114]
    done_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert done_snapshot == result
    assert "llm_trace" in result["payload"]
    assert "synthesis" in result["payload"]["llm_trace"]
    assert "reflection" in result["payload"]["llm_trace"]

    step_snapshots_dir = tmp_path / "data" / "local_tests" / "personal_companion" / "orchestrator" / "turn_steps" / str(queued.job_id)
    assert step_snapshots_dir.exists()
    step_snapshot_files = sorted(step_snapshots_dir.glob("*.json"))
    assert len(step_snapshot_files) >= 4
    sample_step_snapshot = json.loads(step_snapshot_files[-1].read_text(encoding="utf-8"))
    assert sample_step_snapshot["turn_result_response"]["job_id"] == queued.job_id
    assert "payload" in sample_step_snapshot["turn_result_response"]

    chat_file = service.store.read_chat_file(12, companion.id, chat.session_id)
    assert chat_file is not None
    assert chat_file["messages"]
    assert bootstrap.existing_chat_count == 0
    assert bootstrap.chat_limit == 10

    frontend_config = service.frontend_config_view()
    assert "ports" not in frontend_config
    assert "base_url" not in frontend_config
    assert frontend_config["models"]["personal_companion_routing"] == {
        "provider": "ollama_cloud",
        "name": "gemma3:4b",
    }
    assert frontend_config["models"]["personal_companion_policy"] == {
        "provider": "ollama_cloud",
        "name": "gemma3:4b",
    }
    assert frontend_config["models"]["personal_companion_reflection"] == {
        "provider": "ollama_cloud",
        "name": "gemma3:4b",
    }
    assert frontend_config["endpoints"]["queue_turn"] == "/users/me/companion/orchestrator/chats/{session_id}/turns"


class WeakCanonProviderClient(FakeProviderClient):
    async def run_elder(
        self,
        *,
        user_id: int,
        agent_id: str,
        query: str,
        ontology_id: int,
        auth_header: str | None = None,
    ) -> dict[str, Any]:
        self.elder_queries.append(query)
        self.elder_started.set()
        await self.allow_elder_finish.wait()
        return {
            "ok": True,
            "agent_id": agent_id,
            "agent_name": "Elder",
            "agent_job": "elder",
            "answer": "The records do not say enough about Ernst to ground a role or personality.",
            "sources": [],
        }


@pytest.mark.asyncio
async def test_companion_service_stops_before_librarian_when_canon_is_weak(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = WeakCanonProviderClient()
    service.provider_client = provider
    service.llm_client = FakeLLMClient()
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)
    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="Based on the game rules, which occupations could Ernst have?",
    )
    await asyncio.wait_for(provider.elder_started.wait(), timeout=1)
    provider.allow_elder_finish.set()

    result = None
    for _ in range(20):
        result = service.store.get_turn_job(12, queued.job_id)
        if result and result["status"] == "done":
            break
        await asyncio.sleep(0.05)

    assert result is not None
    assert result["status"] == "done"
    assert provider.librarian_queries
    assert len(result["payload"]["execution"]["completed_steps"]) == 2
    assert result["payload"]["execution"]["stopped_reason"] is None


@pytest.mark.asyncio
async def test_generic_rules_query_routes_to_librarian_only_and_preserves_book_links(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = MultiBookProviderClient()
    service.provider_client = provider
    service.llm_client = GenericRulesLLMClient()
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)
    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="And, how a character would recover Sanity?",
    )

    result = None
    for _ in range(20):
        result = service.store.get_turn_job(12, queued.job_id)
        if result and result["status"] == "done":
            break
        await asyncio.sleep(0.05)

    assert result is not None
    assert result["status"] == "done"
    assert provider.elder_queries == []
    assert len(provider.librarian_queries) == 1
    assert result["payload"]["routing"] == {
        "use_elder": False,
        "use_librarian": True,
        "reason": "generic_rules_librarian_only",
    }
    assert result["payload"]["plan"]["steps"] == [
        {
            "step_id": "step-1",
            "tool_job": "librarian",
            "goal": "Determine the rules for Sanity recovery.",
            "query": "What are the mechanics for recovering Sanity in the game system?",
            "depends_on": [],
            "use_prior_context": False,
            "success_requirements": ["Sanity recovery mechanics"],
            "on_failure": "stop",
        }
    ]
    assert len(result["payload"]["agent_responses"]) == 1
    sources = result["payload"]["agent_responses"][0]["sources"]
    assert [source["library_item_id"] for source in sources] == [2, 3]
    assert all(source["source_type"] == "book" for source in sources)
    assert result["payload"]["final"]["references"]["book_sources"] == [
        {
            "source_type": "book",
            "library_item_id": 2,
            "book_title": "CoC Investigator Rulebook",
            "book_authors": "Chaosium",
            "ontology_id": 99,
            "pdf_url": "http://localhost:8100/media/library/99/2/content.pdf",
            "page_urls": ["http://localhost:8100/media/library/99/2/content.pdf#page=114"],
            "pages": [114],
            "excerpt_count": 1,
            "agent_id": "librarian-1",
            "agent_name": "Librarian",
        },
        {
            "source_type": "book",
            "library_item_id": 3,
            "book_title": "Keeper Rulebook",
            "book_authors": "Chaosium",
            "ontology_id": 99,
            "pdf_url": "http://localhost:8100/media/library/99/3/content.pdf",
            "page_urls": ["http://localhost:8100/media/library/99/3/content.pdf#page=167"],
            "pages": [167],
            "excerpt_count": 1,
            "agent_id": "librarian-1",
            "agent_name": "Librarian",
        },
    ]
    assert len(result["payload"]["final"]["references"]["book_links"]) == 2
    assert 'data-source-type="book"' in result["payload"]["final"]["linked_text"]
    assert 'data-library-item-id="2"' in result["payload"]["final"]["linked_text"]
    assert 'data-library-item-id="3"' in result["payload"]["final"]["linked_text"]
    assert 'data-ontology-id="99"' in result["payload"]["final"]["linked_text"]
    assert "CoC Investigator Rulebook, p.114" in result["payload"]["final"]["text"]
    assert "Keeper Rulebook, p.167" in result["payload"]["final"]["text"]


@pytest.mark.asyncio
async def test_conversation_memory_rewrites_pronoun_followup_and_persists_memory(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = FakeProviderClient()
    llm = MemoryAwareLLMClient()
    service.provider_client = provider
    service.llm_client = llm
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)

    first = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="Who is Ernst?",
    )
    await asyncio.wait_for(provider.elder_started.wait(), timeout=1)
    provider.allow_elder_finish.set()
    for _ in range(20):
        done = service.store.get_turn_job(12, first.job_id)
        if done and done["status"] == "done":
            break
        await asyncio.sleep(0.05)

    provider.elder_started = asyncio.Event()
    provider.allow_elder_finish = asyncio.Event()
    second = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="What would help him?",
    )
    await asyncio.wait_for(provider.elder_started.wait(), timeout=1)
    provider.allow_elder_finish.set()
    result = None
    for _ in range(20):
        result = service.store.get_turn_job(12, second.job_id)
        if result and result["status"] == "done":
            break
        await asyncio.sleep(0.05)

    assert result is not None
    assert result["payload"]["conversation_context"]["resolved_subject"] == "Ernst"
    assert result["payload"]["conversation_context"]["rewritten_query"] == "What would help Ernst?"
    assert any("Ernst" in query for query in provider.elder_queries + provider.librarian_queries)
    chat_file = service.store.read_chat_file(12, companion.id, chat.session_id)
    assert chat_file is not None
    assert chat_file["memory"]["last_resolved_subject"] == "Ernst"
    assert chat_file["memory"]["active_entities"][0]["name"] == "Ernst"
    assert llm.planning_prompts
    assert "recent conversation" in llm.planning_prompts[-1].lower()


@pytest.mark.asyncio
async def test_conversation_memory_uses_bounded_recent_window_and_summary(tmp_path):
    settings = Settings(
        data_dir=str(tmp_path / "data"),
        media_root=str(tmp_path / "media"),
        conversation_recent_messages_limit=6,
        conversation_summary_trigger_messages=10,
        conversation_context_char_limit=300,
    )
    service = CompanionService(settings)
    provider = FakeProviderClient()
    llm = MemoryAwareLLMClient()
    service.provider_client = provider
    service.llm_client = llm
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)
    for index in range(12):
        service.store.append_chat_message(
            user_id=12,
            companion_id=companion.id,
            session_id=chat.session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index} " + ("x" * 40),
        )
    service.store.update_chat_memory(
        user_id=12,
        companion_id=companion.id,
        session_id=chat.session_id,
        memory={
            "summary": "older summary about Ernst and prior decisions",
            "active_entities": [{"name": "Ernst", "type": "subject", "confidence": 0.9}],
            "open_topics": ["equipment"],
            "last_resolved_subject": "Ernst",
        },
    )

    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="What would help him?",
    )
    running = service.store.get_turn_job(12, queued.job_id)
    assert running is not None
    context = running["payload"]["conversation_context"]
    assert len(context["recent_messages_used"]) <= 6
    assert context["summary_used"] == "older summary about Ernst and prior decisions"
    assert "Ernst" in context["rewritten_query"]


@pytest.mark.asyncio
async def test_conversation_memory_ambiguity_uses_most_recent_active_subject(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = FakeProviderClient()
    llm = MemoryAwareLLMClient()
    service.provider_client = provider
    service.llm_client = llm
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)
    service.store.update_chat_memory(
        user_id=12,
        companion_id=companion.id,
        session_id=chat.session_id,
        memory={
            "summary": "",
            "active_entities": [
                {"name": "Hans", "type": "subject", "confidence": 0.95},
                {"name": "Ernst", "type": "subject", "confidence": 0.9},
            ],
            "open_topics": [],
            "last_resolved_subject": "Hans",
        },
    )
    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="What would help him?",
    )
    running = service.store.get_turn_job(12, queued.job_id)
    assert running is not None
    assert running["payload"]["conversation_context"]["resolved_subject"] == "Hans"


@pytest.mark.asyncio
async def test_chat_session_crud_and_counts(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    service.provider_client = FakeProviderClient()
    service.orchestrator.provider_client = service.provider_client
    service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )

    bootstrap = await service.bootstrap_world(user_id=12, ontology_id=99)
    assert bootstrap.existing_chat_count == 0
    assert bootstrap.chat_limit == 10

    created = await service.create_chat_session(
        user_id=12,
        payload=CompanionChatSessionCreateRequest(ontology_id=99),
    )
    assert created.title == "New chat"
    assert created.message_count == 0

    listed = service.list_chat_sessions(user_id=12, ontology_id=99)
    assert [item.session_id for item in listed] == [created.session_id]

    renamed = service.update_chat_session(
        user_id=12,
        session_id=created.session_id,
        payload=CompanionChatSessionUpdateRequest(title="Ernst thread"),
    )
    assert renamed.title == "Ernst thread"

    counts = service.chat_session_counts(user_id=12)
    assert len(counts) == 1
    assert counts[0].ontology_id == 99
    assert counts[0].count == 1
    assert counts[0].limit == 10

    service.delete_chat_session(user_id=12, session_id=created.session_id)
    assert service.list_chat_sessions(user_id=12, ontology_id=99) == []


@pytest.mark.asyncio
async def test_chat_sessions_keep_memory_isolated_within_same_ontology(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = FakeProviderClient()
    llm = MemoryAwareLLMClient()
    service.provider_client = provider
    service.llm_client = llm
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client
    companion = service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )

    await service.bootstrap_world(user_id=12, ontology_id=99)
    chat_a = await _create_chat(service, user_id=12, ontology_id=99, title="Chat A")
    chat_b = await _create_chat(service, user_id=12, ontology_id=99, title="Chat B")

    service.store.update_chat_memory(
        user_id=12,
        companion_id=companion.id,
        session_id=chat_a.session_id,
        memory={
            "summary": "Older discussion about Ernst",
            "active_entities": [{"name": "Ernst", "type": "subject", "confidence": 0.9}],
            "open_topics": ["equipment"],
            "last_resolved_subject": "Ernst",
        },
    )
    service.store.update_chat_memory(
        user_id=12,
        companion_id=companion.id,
        session_id=chat_b.session_id,
        memory={
            "summary": "Older discussion about Hans",
            "active_entities": [{"name": "Hans", "type": "subject", "confidence": 0.9}],
            "open_topics": ["equipment"],
            "last_resolved_subject": "Hans",
        },
    )

    queued_a = await service.queue_turn(user_id=12, session_id=chat_a.session_id, query="What would help him?")
    running_a = service.store.get_turn_job(12, queued_a.job_id)
    assert running_a is not None
    assert running_a["payload"]["conversation_context"]["resolved_subject"] == "Ernst"

    queued_b = await service.queue_turn(user_id=12, session_id=chat_b.session_id, query="What would help him?")
    running_b = service.store.get_turn_job(12, queued_b.job_id)
    assert running_b is not None
    assert running_b["payload"]["conversation_context"]["resolved_subject"] == "Hans"


@pytest.mark.asyncio
async def test_planner_still_runs_and_selects_tools_when_policy_disables_them(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    provider = FakeProviderClient()
    llm = PolicyDisablesKnowledgeLLMClient()
    service.provider_client = provider
    service.llm_client = llm
    service.orchestrator.provider_client = service.provider_client
    service.orchestrator.llm_client = service.llm_client

    service.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    await service.bootstrap_world(user_id=12, ontology_id=99)
    chat = await _create_chat(service, user_id=12, ontology_id=99)

    queued = await service.queue_turn(
        user_id=12,
        session_id=chat.session_id,
        query="I have a couple questions about Ernst: Is he a human?",
    )

    await asyncio.wait_for(provider.elder_started.wait(), timeout=1)
    provider.allow_elder_finish.set()

    result = None
    for _ in range(20):
        result = service.store.get_turn_job(12, queued.job_id)
        if result and result["status"] == "done":
            break
        await asyncio.sleep(0.05)

    assert result is not None
    assert provider.elder_queries
    assert llm.planning_prompts
    assert "Available tools for this session:" in llm.planning_prompts[0]
    assert "Companion policy summary:" in llm.planning_prompts[0]
    assert result["payload"]["routing"]["use_elder"] is True
    assert result["payload"]["routing"]["use_librarian"] is False


class FakeUpload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _png_upload(color: tuple[int, int, int]) -> FakeUpload:
    buffer = BytesIO()
    Image.new("RGB", (12, 12), color).save(buffer, format="PNG")
    return FakeUpload(buffer.getvalue())


@pytest.mark.asyncio
async def test_companion_avatar_upload_uses_stable_username_media_path(tmp_path):
    service = CompanionService(Settings(data_dir=str(tmp_path / "data"), media_root=str(tmp_path / "media")))
    service.create_companion(
        12,
        PersonalCompanionAgentCreate(
            name="Fiona",
            writing_style="Concise and grounded.",
            active=True,
        ),
    )

    first = await service.upload_companion_avatar(
        user_id=12,
        username="Princess Fiona",
        file=_png_upload((255, 0, 0)),
    )
    target_path = tmp_path / "media" / "princess-fiona" / "companion.png"
    first_bytes = target_path.read_bytes()

    second = await service.upload_companion_avatar(
        user_id=12,
        username="Princess Fiona",
        file=_png_upload((0, 255, 0)),
    )

    assert first.avatar_url == "/media/princess-fiona/companion.png"
    assert second.avatar_url == "/media/princess-fiona/companion.png"
    assert service.get_companion(12).avatar_url == "/media/princess-fiona/companion.png"
    assert target_path.exists()
    assert target_path.read_bytes() != first_bytes
