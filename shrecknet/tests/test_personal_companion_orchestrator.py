from __future__ import annotations

import asyncio

import pytest

from app.jobs.personal_companion.prompts import ROUTING_PROMPT
from app.jobs.personal_companion.personal_companion_orchestrator import (
    PersonalCompanionOrchestrator,
)


class _NoopLLMClient:
    async def chat(self, *args, **kwargs):  # pragma: no cover - not used in these tests
        return "ok"


def test_routing_prompt_format_does_not_raise_and_preserves_json_shape() -> None:
    rendered = ROUTING_PROMPT.format(query="What rules apply?")
    assert '"use_elder"' in rendered
    assert '"use_librarian"' in rendered


@pytest.mark.asyncio
async def test_mixed_elder_librarian_parallel_fanout_and_payload() -> None:
    orchestrator = PersonalCompanionOrchestrator(llm_client=_NoopLLMClient())

    calls: list[str] = []

    async def elder_runner(agent_id: str):
        await asyncio.sleep(0.01)
        calls.append(f"elder:{agent_id}")
        return {
            "agent_id": agent_id,
            "agent_name": "Elder One",
            "agent_job": "elder",
            "ok": True,
            "answer": "Tamura revealed her pregnancy to Lynelle.",
            "sources": [
                {
                    "node_id": "n1",
                    "node_label": "Scene",
                    "node_name": "Campfire Reveal",
                    "score": 0.88,
                    "evidence_chunks": [{"text": "Tamura revealed it."}],
                }
            ],
        }

    async def librarian_runner(agent_id: str):
        await asyncio.sleep(0.01)
        calls.append(f"librarian:{agent_id}")
        return {
            "agent_id": agent_id,
            "agent_name": "Librarian One",
            "agent_job": "librarian",
            "ok": True,
            "answer": "Pregnancy applies a dexterity penalty of 2 points.",
            "sources": [
                {
                    "library_item_id": 7,
                    "book_title": "Pendragon Core",
                    "page_number": 13,
                    "score": 0.91,
                    "page_url": "https://example.test/book/7/page/13",
                }
            ],
        }

    responses = await orchestrator.fanout_tools(
        selected_elder_ids=["elder-1"],
        selected_librarian_ids=["librarian-1"],
        elder_runner=elder_runner,
        librarian_runner=librarian_runner,
    )

    assert len(responses) == 2
    assert "elder:elder-1" in calls
    assert "librarian:librarian-1" in calls

    payload = orchestrator.build_turn_payload(
        session_id="sess-1",
        query="Did Tamura reveal her pregnancy and what is the dexterity impact?",
        routing={"use_elder": True, "use_librarian": True, "reason": "mixed"},
        selected_tools={"elder": ["elder-1"], "librarian": ["librarian-1"]},
        agent_responses=responses,
        final_text="Tamura revealed her pregnancy, and rules indicate a dexterity penalty.",
    )

    assert payload["selected_tools"]["elder"] == ["elder-1"]
    assert payload["selected_tools"]["librarian"] == ["librarian-1"]
    assert payload["sources"]
    assert payload["claims"]
    assert "Claim Anchors" in payload["final"]["annotated_text"]


def test_contradiction_and_partial_failure_are_reported() -> None:
    orchestrator = PersonalCompanionOrchestrator(llm_client=_NoopLLMClient())

    responses = [
        {
            "agent_id": "elder-1",
            "agent_name": "Elder One",
            "agent_job": "elder",
            "ok": True,
            "answer": "Tamura dexterity is reduced by 2 while pregnant.",
            "sources": [
                {
                    "node_id": "n1",
                    "node_label": "Scene",
                    "node_name": "Campfire",
                    "score": 0.82,
                    "evidence_chunks": [{"text": "reduced by 2"}],
                }
            ],
        },
        {
            "agent_id": "librarian-1",
            "agent_name": "Librarian One",
            "agent_job": "librarian",
            "ok": True,
            "answer": "Dexterity is not reduced during pregnancy in this optional rule set.",
            "sources": [
                {
                    "library_item_id": 8,
                    "book_title": "Optional Rules",
                    "page_number": 44,
                    "score": 0.75,
                }
            ],
        },
        {
            "agent_id": "librarian-2",
            "agent_name": "Librarian Two",
            "agent_job": "librarian",
            "ok": False,
            "error": "timeout",
            "sources": [],
        },
    ]

    payload = orchestrator.build_turn_payload(
        session_id="sess-2",
        query="How does pregnancy affect dexterity?",
        routing={"use_elder": True, "use_librarian": True, "reason": "mixed"},
        selected_tools={"elder": ["elder-1"], "librarian": ["librarian-1", "librarian-2"]},
        agent_responses=responses,
        final_text="Sources disagree on whether dexterity is reduced.",
    )

    assert payload["status"] == "done"
    assert payload["tool_failures"]
    assert payload["tool_failures"][0]["error"] == "timeout"
    assert payload["analysis"]["contradictions"]
