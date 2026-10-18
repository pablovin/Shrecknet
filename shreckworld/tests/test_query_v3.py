from __future__ import annotations

import json

import pytest

from app.jobs.shreckworld_agent.agent import (
    _deterministic_plan, _make_coverage, _plan_query, _requires_planning, _review_evidence,
)
from app.schemas.query_v3 import EvidenceGroup
from app.schemas.query_v3 import QueryV3Trace
from app.schemas.shreckworld import (
    ShreckWorldCitation, ShreckWorldModelTarget, ShreckWorldQueryRequest,
)


MODEL_TARGET = ShreckWorldModelTarget(provider="openai", name="gpt-5-nano")


def test_deterministic_planning_keeps_definition_on_zero_llm_path():
    plan = _deterministic_plan("What is Sanity?")
    assert plan.intent == "simple"
    assert len(plan.needs) == 1
    assert plan.needs[0].kind == "definition"
    assert not _requires_planning("What is Sanity?")


def test_recovery_of_sanity_tracks_san_points_and_insanity_separately():
    plan = _deterministic_plan("How do you recover Sanity?")
    assert [need.id for need in plan.needs] == ["need-1", "need-2"]
    assert "numerical" in plan.needs[0].question
    assert "insanity" in plan.needs[1].question


def test_complex_and_exhaustive_questions_route_to_planner():
    assert _requires_planning("Can psychotherapy and private care be combined?")
    assert _requires_planning("What are all the ways to recover?")


def test_query_interpretation_corrects_only_unique_source_scoped_recovery_terms():
    from app.jobs.shreckworld_agent.interpretation import interpret_query
    from app.services.query import RetrievalScope

    item = type("Source", (), {"title": "Core Rules", "authors": ""})()
    chunk = type("Chunk", (), {
        "text": "Sanity is a measure of stability. Sanity losses affect characters.",
        "heading_path": [],
    })()
    scope = RetrievalScope(
        session=None, shreckworld_id="world", fingerprint="f" * 64,
        document_ids=[], rows=[(chunk, item)], by_id={}, trace={},
    )

    corrected = interpret_query("How do I heal Saniyt?", scope)
    assert corrected.interpreted_query == "How do I heal Sanity?"
    assert corrected.corrections[0].basis == "vocabulary_fuzzy"
    assert interpret_query("Does Saniyt cause a wound?", scope).corrections == []

    ambiguous_chunk = type("Chunk", (), {
        "text": "Sanity affects the mind. Sanity changes over time. Saniy is another term. Saniy is used here.",
        "heading_path": [],
    })()
    ambiguous_scope = RetrievalScope(
        session=None, shreckworld_id="world", fingerprint="f" * 64,
        document_ids=[], rows=[(ambiguous_chunk, item)], by_id={}, trace={},
    )
    assert interpret_query("How do I heal Saniyt?", ambiguous_scope).corrections == []


def test_coverage_review_requires_verbatim_quote_and_known_citation(monkeypatch):
    plan = _deterministic_plan("What is Sanity?")
    citation = ShreckWorldCitation(
        source_id="source-1", library_item_id="book", title="Core Rules",
        page_number=8, chunk_id="chunk-1", score=0.9,
        excerpt="Sanity is a measure of mental stability.",
        authority_tier="core_rules", authority_priority=10,
        evidence_text="Sanity is a measure of mental stability.",
    )
    result = {
        "needs": [{
            "need_id": "need-1", "supported_requirements": ["Direct definition"],
            "missing_requirements": [], "evidence": [{
                "source_id": "source-1", "quote": "Sanity is a measure of mental stability.",
            }], "explanation": "The passage defines Sanity.",
        }],
    }
    monkeypatch.setattr(
        "app.jobs.shreckworld_agent.agent.generate_shreckworld_structured_output",
        lambda **kwargs: json.dumps(result),
    )
    groups = [EvidenceGroup(evidence_id="source-1", need_ids=["need-1"], chunk_ids=["chunk-1"])]
    assert _review_evidence(
        plan, [citation], groups, MODEL_TARGET,
    )["need-1"].supported_requirements == ["Direct definition"]
    result["needs"][0]["evidence"][0]["quote"] = "Sanity is a measure of luck."
    with pytest.raises(ValueError, match="verbatim"):
        _review_evidence(plan, [citation], groups, MODEL_TARGET)


def test_absence_of_combination_rule_is_unresolved():
    from app.schemas.query_v3 import QueryPlan

    plan = QueryPlan(
        intent="multi_rule",
        needs=[{
            "id": "need-1", "question": "Can both treatments be combined?",
            "kind": "condition", "required": True,
            "coverage_requirement": "Whether the treatments may be used together.",
            "priority": 1,
        }],
    )
    from app.jobs.shreckworld_agent.agent import _ReviewedNeed
    reviews = {"need-1": _ReviewedNeed(
        need_id="need-1", supported_requirements=["Both treatments are described separately."],
        missing_requirements=["The text does not establish whether to combine them."],
        evidence=[], explanation="No combination rule is established in the quoted evidence.",
    )}
    coverage = _make_coverage(plan, [], reviews, planner_fallback=False)
    assert coverage.needs[0].status == "unresolved"
    assert coverage.status == "unresolved"


def test_planner_failure_uses_diagnostic_deterministic_fallback(monkeypatch):
    from app.integrations.shreckllm import ShreckLLMUnavailable

    class EmptySession:
        def scalars(self, statement):
            return iter(())

    def unavailable(**kwargs):
        raise ShreckLLMUnavailable("test unavailable")

    monkeypatch.setattr(
        "app.jobs.shreckworld_agent.agent.generate_shreckworld_structured_output", unavailable,
    )
    trace = QueryV3Trace()
    plan, fallback = _plan_query(
        EmptySession(), world=type("World", (), {"id": "world", "name": "Test"})(),
        request=ShreckWorldQueryRequest(query="Compare the recovery procedures and treatment conditions."),
        trace=trace,
        model_target=MODEL_TARGET,
    )
    assert fallback is True
    assert trace.planner_llm_calls == 1
    assert trace.planner_fallback is True
    assert len(plan.needs) == 1


def test_agent_job_execution_failure_is_persisted(api, monkeypatch):
    client, headers, world_id = api
    from app.tasks.agent import run_shreckworld_agent_job

    monkeypatch.setattr(run_shreckworld_agent_job, "delay", lambda job_id: None)
    created = client.post(
        f"/shreckworlds/{world_id}/agent-jobs", headers=headers,
        json={"query": "What is Sanity?"},
    )
    job_id = created.json()["id"]

    def fail(*args, **kwargs):
        raise RuntimeError("worker failure")

    monkeypatch.setattr("app.tasks.agent.run_shreckworld_agent", fail)
    with pytest.raises(RuntimeError, match="worker failure"):
        run_shreckworld_agent_job.run(job_id)
    status = client.get(f"/shreckworlds/{world_id}/agent-jobs/{job_id}", headers=headers).json()
    assert status["status"] == "failed"
    assert status["error"] == "The ShreckWorld agent could not complete this query. Retry the request."


def test_shared_parent_is_retained_once_for_every_supporting_need(api, monkeypatch):
    from types import SimpleNamespace

    from app.db.session import session_factory
    from app.jobs.shreckworld_agent.agent import RetrievalScope, run_shreckworld_agent
    from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem
    from app.schemas.query_v3 import QueryNeed, QueryPlan
    from app.schemas.shreckworld import ShreckWorldCitation, ShreckWorldQueryRequest

    _, _, world_id = api
    with session_factory()() as session:
        item = ShreckWorldLibraryItem(
            shreckworld_id=world_id, title="Test Rules", authority_tier="core_rules",
            authority_priority=10, pdf_path="/test.pdf", source_sha256="a" * 64,
            embedding_status="ready",
        )
        session.add(item)
        session.flush()
        first = ShreckWorldDocumentChunk(
            library_item_id=item.id, page_number=1, ordinal=1, text="Treatment takes time.",
            embedding_json="[]", parent_chunk_id="shared-parent",
        )
        second = ShreckWorldDocumentChunk(
            library_item_id=item.id, page_number=1, ordinal=2, text="Treatment requires a check.",
            embedding_json="[]", parent_chunk_id="shared-parent",
        )
        session.add_all([first, second])
        session.commit()
        session.refresh(item)
        session.refresh(first)
        session.refresh(second)
        scope = RetrievalScope(
            session=session, shreckworld_id=world_id, fingerprint="f" * 64,
            document_ids=[], rows=[(first, item), (second, item)],
            by_id={first.id: (first, item), second.id: (second, item)},
            trace={
                "requested_sources": 1, "sources_searched": 1,
                "sources_without_ready_index": 0, "sources_requiring_reindex": 0,
            },
        )
        plan = QueryPlan(
            intent="multi_rule",
            needs=[
                QueryNeed(
                    id="need-1", question="When does treatment take time?",
                    kind="procedure", required=True, coverage_requirement="Treatment duration.",
                    priority=1,
                ),
                QueryNeed(
                    id="need-2", question="Does treatment require a check?",
                    kind="mechanic", required=True, coverage_requirement="Treatment check.",
                    priority=2,
                ),
            ],
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent._plan_query",
            lambda *args, **kwargs: (plan, False),
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.resolve_retrieval_scope",
            lambda *args, **kwargs: scope,
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.get_settings",
            lambda: SimpleNamespace(
                query_v3_coverage_review_enabled=False, query_v3_evidence_token_budget=1000,
            ),
        )

        def candidates(_scope, query, *, limit):
            chunk = first if query == plan.needs[0].question else second
            if "Treatment duration." in query or "Treatment check." in query:
                return [], {"qdrant_fallback": False, "reranker_fallback": False}
            return [{"chunk_id": chunk.id, "rrf_score": 1.0}], {
                "qdrant_fallback": False, "reranker_fallback": False,
            }

        monkeypatch.setattr("app.jobs.shreckworld_agent.agent.search_candidates", candidates)

        def hydrate(_scope, candidate, *, number, complete_evidence):
            chunk, source = scope.by_id[candidate["chunk_id"]]
            return ShreckWorldCitation(
                source_id=f"source-{number}", library_item_id=source.id, title=source.title,
                page_number=chunk.page_number, chunk_id=chunk.id, score=1.0,
                excerpt=chunk.text, evidence_text="Complete shared treatment section.",
                authority_tier="core_rules", authority_priority=10,
            )

        monkeypatch.setattr("app.jobs.shreckworld_agent.agent.hydrate_candidate", hydrate)
        response = run_shreckworld_agent(
            session, shreckworld_id=world_id,
            request=ShreckWorldQueryRequest(query="Explain both treatment facts.", top_k=4),
        )
    assert len(response.citations) == 1
    assert len(response.evidence_groups) == 1
    assert set(response.evidence_groups[0].need_ids) == {"need-1", "need-2"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    from test_shreckworld_api import _client

    client, headers = _client(monkeypatch, tmp_path)
    world = client.post(
        "/admin/shreckworlds", headers=headers,
        json={"name": "Query V3", "shrecknet_world_id": "query-v3"},
    ).json()
    yield client, headers, world["id"]
    from app.main import app
    app.dependency_overrides.clear()
    client.close()


def test_agent_job_lifecycle_and_owner_isolation(api, monkeypatch):
    client, headers, world_id = api
    from app.jobs.shreckworld_agent.agent import RetrievalScope
    from app.main import app
    from app.api.deps import ShrecknetPrincipal, get_current_user
    from app.tasks.agent import run_shreckworld_agent_job

    monkeypatch.setattr(run_shreckworld_agent_job, "delay", lambda job_id: None)

    def empty_scope(session, *, shreckworld_id, **kwargs):
        return RetrievalScope(
            session=session, shreckworld_id=shreckworld_id, fingerprint="f" * 64,
            document_ids=[], rows=[], by_id={},
            trace={
                "requested_sources": 0, "sources_searched": 0, "sources_without_ready_index": 0,
                "sources_requiring_reindex": 0,
            },
        )

    monkeypatch.setattr("app.jobs.shreckworld_agent.agent.resolve_retrieval_scope", empty_scope)
    created = client.post(
        f"/shreckworlds/{world_id}/agent-jobs", headers=headers,
        json={"query": "What is Sanity?"},
    )
    assert created.status_code == 202, created.text
    queued = created.json()
    assert queued["status"] == "queued"
    assert queued["stage"] == "queued"

    run_shreckworld_agent_job.run(queued["id"])
    completed = client.get(
        f"/shreckworlds/{world_id}/agent-jobs/{queued['id']}", headers=headers,
    )
    assert completed.status_code == 200, completed.text
    result = completed.json()
    assert result["status"] == "completed"
    assert result["result"]["retrieval_strategy"] == "planned"
    assert result["result"]["query_plan"]["needs"][0]["kind"] == "definition"
    assert result["result"]["evidence_coverage"]["needs"][0]["status"] == "missing"

    answer_job = client.post(
        f"/shreckworlds/{world_id}/agent-jobs", headers=headers,
        json={"query": "What is Sanity?", "mode": "answer"},
    )
    assert answer_job.status_code == 202, answer_job.text
    run_shreckworld_agent_job.run(answer_job.json()["id"])
    completed_answer = client.get(
        f"/shreckworlds/{world_id}/agent-jobs/{answer_job.json()['id']}", headers=headers,
    ).json()
    assert completed_answer["result"]["mode"] == "answer"
    assert completed_answer["result"]["answer_status"] == "unsupported"
    assert completed_answer["result"]["answer_content"]["direct_answer"]

    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="another-user", role="player")
    assert client.get(
        f"/shreckworlds/{world_id}/agent-jobs/{queued['id']}", headers=headers,
    ).status_code == 404


def test_agent_queue_failure_is_persisted(api, monkeypatch):
    client, headers, world_id = api
    from sqlalchemy import select
    from app.db.session import session_factory
    from app.models import ShreckWorldAgentJob
    from app.tasks.agent import run_shreckworld_agent_job

    def fail_queue(job_id):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(run_shreckworld_agent_job, "delay", fail_queue)
    response = client.post(
        f"/shreckworlds/{world_id}/agent-jobs", headers=headers,
        json={"query": "What is Sanity?"},
    )
    assert response.status_code == 503
    with session_factory()() as session:
        job = session.scalar(select(ShreckWorldAgentJob))
        assert job is not None
        assert job.status == "failed"
        assert job.stage == "failed"
        assert job.error == "The agent job could not be queued. Please retry."


def test_synchronous_planned_query_uses_evidence_only_pipeline(api, monkeypatch):
    client, headers, world_id = api
    from app.jobs.shreckworld_agent.agent import RetrievalScope

    def empty_scope(session, *, shreckworld_id, **kwargs):
        return RetrievalScope(
            session=session, shreckworld_id=shreckworld_id, fingerprint="f" * 64,
            document_ids=[], rows=[], by_id={},
            trace={
                "requested_sources": 0, "sources_searched": 0, "sources_without_ready_index": 0,
                "sources_requiring_reindex": 0,
            },
        )

    monkeypatch.setattr("app.jobs.shreckworld_agent.agent.resolve_retrieval_scope", empty_scope)
    response = client.post(
        f"/shreckworlds/{world_id}/query", headers=headers,
        json={"query": "What is Sanity?", "retrieval_strategy": "planned", "include_trace": True},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["retrieval_strategy"] == "planned"
    assert result["mode"] == "evidence"
    assert "No answer synthesis was run" in result["answer"]
    assert result["query_plan"]["needs"][0]["kind"] == "definition"
    assert result["v3_trace"]["planner_llm_calls"] == 0
    assert result["evidence_coverage"]["needs"][0]["status"] == "missing"


def test_planned_answer_mode_is_accepted_and_uses_one_planned_run(api, monkeypatch):
    client, headers, world_id = api
    from app.schemas.shreckworld import (
        AnswerPresentation, QuerySourceCoverage, ShreckWorldQueryResponse,
    )
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        return ShreckWorldQueryResponse(
            shreckworld_id=world_id, query="What is Sanity?",
            answer="Sanity [source-1]", citations=[], mode="answer",
            answer_status="complete",
            answer_content=AnswerPresentation(direct_answer="Sanity [source-1]"),
            source_coverage=QuerySourceCoverage(
                requested_source_count=0, searched_source_count=0,
                sources_without_ready_index=0, sources_requiring_reindex=0,
            ),
            retrieval_strategy="planned",
        )
    monkeypatch.setattr("app.api.routers.shreckworlds.run_shreckworld_agent", run)
    response = client.post(
        f"/shreckworlds/{world_id}/query", headers=headers,
        json={"query": "What is Sanity?", "retrieval_strategy": "planned", "mode": "answer"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "answer"
    assert response.json()["answer_content"]["direct_answer"] == "Sanity [source-1]"
    assert len(calls) == 1


def test_planned_answer_synthesizes_once_from_retrieved_citations(api, monkeypatch):
    from types import SimpleNamespace

    from app.db.session import session_factory
    from app.jobs.shreckworld_agent.agent import (
        RetrievalScope, _ReviewedEvidence, _ReviewedNeed, run_shreckworld_agent,
    )
    from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem
    from app.schemas.shreckworld import (
        AnswerPresentation, ClaimEvidenceSupport, CitationLocation, GroundedClaim,
        ShreckWorldCitation, ShreckWorldQueryRequest,
    )
    from app.services.grounded_answer import evidence_item

    _, _, world_id = api
    synthesis_calls = []
    with session_factory()() as session:
        item = ShreckWorldLibraryItem(
            shreckworld_id=world_id, title="Core Rules", authority_tier="core_rules",
            authority_priority=10, pdf_path="/test.pdf", source_sha256="a" * 64,
            embedding_status="ready",
        )
        session.add(item)
        session.flush()
        chunk = ShreckWorldDocumentChunk(
            library_item_id=item.id, page_number=12, ordinal=1,
            text="Sanity measures mental stability.", embedding_json="[]",
        )
        session.add(chunk)
        session.commit()
        session.refresh(item)
        session.refresh(chunk)
        scope = RetrievalScope(
            session=session, shreckworld_id=world_id, fingerprint="f" * 64,
            document_ids=[], rows=[(chunk, item)], by_id={chunk.id: (chunk, item)},
            trace={
                "requested_sources": 1, "sources_searched": 1,
                "sources_without_ready_index": 0, "sources_requiring_reindex": 0,
            },
        )
        citation = ShreckWorldCitation(
            source_id="source-1", library_item_id=item.id, title=item.title,
            page_number=12, chunk_id=chunk.id, score=1,
            excerpt=chunk.text, evidence_text=chunk.text,
            authority_tier="core_rules", authority_priority=10,
            source_sha256=item.source_sha256,
        )

        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.resolve_retrieval_scope",
            lambda *args, **kwargs: scope,
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.search_candidates",
            lambda *args, **kwargs: ([{"chunk_id": chunk.id, "rrf_score": 1.0}], {
                "qdrant_fallback": False, "reranker_fallback": False,
            }),
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.hydrate_candidate",
            lambda *args, **kwargs: citation,
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.get_settings",
            lambda: SimpleNamespace(
                query_v3_planning_enabled=True,
                query_v3_coverage_review_enabled=True,
                query_v3_evidence_token_budget=1000,
            ),
        )
        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent._review_evidence",
            lambda *args, **kwargs: {
                "need-1": _ReviewedNeed(
                    need_id="need-1",
                    supported_requirements=["Direct definition"],
                    missing_requirements=[],
                    evidence=[_ReviewedEvidence(
                        source_id="source-1", quote=chunk.text,
                    )],
                    explanation="The passage defines Sanity.",
                ),
            },
        )

        def synthesize(**kwargs):
            synthesis_calls.append(kwargs)
            claim = GroundedClaim(
                id="claim-1", text="Sanity measures mental stability.",
                citation_ids=["source-1"], kind="fact",
                support=[ClaimEvidenceSupport(
                    evidence_id="source-1", quote=chunk.text,
                    location=CitationLocation(precision="section", page_numbers=[12]),
                )],
            )
            presentation = AnswerPresentation(
                direct_answer="Sanity measures mental stability. [source-1]",
            )
            return (
                presentation.direct_answer, "complete", [claim], [], [], 0,
                presentation, [evidence_item(citation)],
            )

        monkeypatch.setattr(
            "app.jobs.shreckworld_agent.agent.synthesize_grounded_answer", synthesize,
        )
        response = run_shreckworld_agent(
            session, shreckworld_id=world_id,
            request=ShreckWorldQueryRequest(
                query="What is Sanity?", mode="answer", retrieval_strategy="planned",
            ),
        )

    assert response.mode == "answer"
    assert response.answer == "Sanity measures mental stability. [source-1]"
    assert response.claims[0].support[0].quote == "Sanity measures mental stability."
    assert response.evidence[0].id == "source-1"
    assert len(synthesis_calls) == 1
    assert synthesis_calls[0]["citations"] == [citation]
    assert synthesis_calls[0]["query"] == "What is Sanity?"
