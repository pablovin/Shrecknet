from types import SimpleNamespace

import pytest

from app.schemas.shreckworld import ShreckWorldCitation
from app.services.grounded_answer import (
    EvidenceBudgetExceeded,
    synthesize_grounded_answer,
)
from app.schemas.shreckworld import ShreckWorldModelTarget

MODEL_TARGET = ShreckWorldModelTarget(provider="openai", name="gpt-5-nano")


def citation():
    return ShreckWorldCitation(
        source_id="source-1",
        library_item_id="book-1",
        title="Core Rules",
        page_number=12,
        chunk_id="chunk-1",
        score=0.9,
        excerpt="The rule excerpt.",
        authority_tier="core_rules",
        authority_priority=10,
        evidence_text="The complete rule section.",
        source_sha256="a" * 64,
    )


def test_grounded_answer_renders_only_claims_citing_supplied_evidence(monkeypatch):
    from app.services import grounded_answer

    monkeypatch.setattr(
        grounded_answer,
        "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=8000),
    )
    monkeypatch.setattr(
        grounded_answer,
        "generate_grounded_answer",
        lambda **kwargs: (
            '{"status":"complete","claims":[{"text":"The rule applies.",'
            '"citation_ids":["source-1"],"kind":"rule","support":[{'
            '"source_id":"source-1","quote":"The complete rule section."}]}],'
            '"missing_information":[],"conflicts":[]}'
        ),
    )

    answer, status, claims, missing, conflicts, omitted, presentation, evidence = synthesize_grounded_answer(
        query="What is the rule?", citations=[citation()], model_target=MODEL_TARGET
    )
    assert answer == "The rule applies. [source-1]"
    assert status == "complete"
    assert claims[0].citation_ids == ["source-1"]
    assert claims[0].id == "claim-1"
    assert claims[0].support[0].quote == "The complete rule section."
    assert presentation.direct_answer == answer
    assert evidence[0].id == "source-1"
    assert missing == [] and conflicts == []
    assert omitted == 0


def test_grounded_answer_discards_claims_with_citations_not_in_evidence(monkeypatch):
    from app.services import grounded_answer

    monkeypatch.setattr(
        grounded_answer,
        "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=8000),
    )
    monkeypatch.setattr(
        grounded_answer,
        "generate_grounded_answer",
        lambda **kwargs: (
            '{"status":"complete","claims":[{"text":"Unsupported claim.",'
            '"citation_ids":["source-99"],"kind":"fact","support":[{'
            '"source_id":"source-99","quote":"untrusted"}]}],'
            '"missing_information":[],"conflicts":[]}'
        ),
    )

    answer, status, claims, missing, _, _, presentation, evidence = synthesize_grounded_answer(
        query="What is the rule?", citations=[citation()], model_target=MODEL_TARGET
    )
    assert status == "unsupported"
    assert claims == []
    assert "[source-99]" not in answer
    assert "could not be verified" in missing[0]
    assert presentation.direct_answer in answer
    assert evidence[0].id == "source-1"


def test_grounded_answer_discards_claims_missing_support_for_any_citation(monkeypatch):
    from app.services import grounded_answer

    second = citation().model_copy(update={
        "source_id": "source-2",
        "library_item_id": "book-2",
        "chunk_id": "chunk-2",
        "evidence_text": "A second relevant rule.",
    })
    monkeypatch.setattr(
        grounded_answer, "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=8000),
    )
    monkeypatch.setattr(
        grounded_answer, "generate_grounded_answer",
        lambda **kwargs: (
            '{"status":"complete","claims":[{"text":"The rule applies.",'
            '"citation_ids":["source-1","source-2"],"kind":"rule","support":[{'
            '"source_id":"source-1","quote":"The complete rule section."}]}],'
            '"missing_information":[],"conflicts":[]}'
        ),
    )

    _, status, claims, missing, _, _, _, _ = synthesize_grounded_answer(
        query="What is the rule?", citations=[citation(), second],
        model_target=MODEL_TARGET,
    )
    assert status == "unsupported"
    assert claims == []
    assert "could not be verified" in missing[0]


def test_grounded_answer_keeps_verified_claims_and_drops_bad_quote_claims(monkeypatch):
    from app.services import grounded_answer

    monkeypatch.setattr(
        grounded_answer, "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=8000),
    )
    monkeypatch.setattr(
        grounded_answer, "generate_grounded_answer",
        lambda **kwargs: (
            '{"status":"complete","claims":['
            '{"text":"Verified rule.","citation_ids":["source-1"],"kind":"rule","support":['
            '{"source_id":"source-1","quote":"The complete rule section."}]},'
            '{"text":"Unverified rule.","citation_ids":["source-1"],"kind":"fact","support":['
            '{"source_id":"source-1","quote":"This quote does not exist."}]}],'
            '"missing_information":[],"conflicts":[]}'
        ),
    )

    answer, status, claims, missing, _, _, _, evidence = synthesize_grounded_answer(
        query="What is the rule?", citations=[citation()], model_target=MODEL_TARGET,
    )
    assert status == "partial"
    assert [claim.text for claim in claims] == ["Verified rule."]
    assert "[source-1]" in answer
    assert "Unverified rule." not in answer
    assert "1 generated claim" in missing[0]
    assert evidence[0].id == "source-1"


def test_grounded_answer_does_not_truncate_passages_to_fit_budget(monkeypatch):
    from app.services import grounded_answer

    monkeypatch.setattr(
        grounded_answer,
        "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=1),
    )
    with pytest.raises(EvidenceBudgetExceeded, match="complete evidence passage"):
        synthesize_grounded_answer(
            query="What is the rule?", citations=[citation()], model_target=MODEL_TARGET
        )


def test_grounded_answer_reports_passages_omitted_by_budget(monkeypatch):
    from app.services import grounded_answer

    second = citation().model_copy(update={
        "source_id": "source-2",
        "library_item_id": "book-2",
        "title": "Supplement",
        "chunk_id": "chunk-2",
        "evidence_text": "x" * 5000,
    })
    monkeypatch.setattr(
        grounded_answer,
        "get_settings",
        lambda: SimpleNamespace(synthesis_evidence_token_budget=1000),
    )
    monkeypatch.setattr(
        grounded_answer,
        "generate_grounded_answer",
        lambda **kwargs: (
            '{"status":"complete","claims":[{"text":"The rule applies.",'
            '"citation_ids":["source-1"],"kind":"rule","support":[{'
            '"source_id":"source-1","quote":"The complete rule section."}]}],'
            '"missing_information":[],"conflicts":[]}'
        ),
    )

    *_, omitted, _, _ = synthesize_grounded_answer(
        query="What is the rule?", citations=[citation(), second], model_target=MODEL_TARGET
    )
    assert omitted == 1


def test_shreckllm_adapter_submits_and_polls_structured_job(monkeypatch):
    from app.integrations import shreckllm

    class Response:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            return None

        def json(self):
            return self.value

    class Client:
        def __init__(self, **kwargs):
            self.headers = kwargs["headers"]
            self.payload = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, *, json, timeout):
            assert path == "/chat/jobs"
            self.payload = json
            return Response({"job_id": "job-1"})

        def get(self, path, *, timeout):
            if path == "/chat/jobs/job-1":
                return Response({"status": "succeeded"})
            assert path == "/chat/jobs/job-1/result"
            return Response({"text": '{"status":"unsupported"}'})

    clients = []

    def make_client(**kwargs):
        client = Client(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(shreckllm, "get_settings", lambda: SimpleNamespace(
        synthesis_provider="openai", synthesis_model="gpt-5-nano",
        shreckllm_base_url="http://llm.test", synthesis_timeout_seconds=5,
        synthesis_poll_interval_seconds=0.1,
    ))
    monkeypatch.setattr(shreckllm.httpx, "Client", make_client)
    monkeypatch.setenv("SHRECKLLM_INTERNAL_SERVICE_TOKEN", "internal-token")

    target = ShreckWorldModelTarget(
        provider="anthropic", name="claude-sonnet-4",
        reasoning=True, reasoning_effort="high", web_search_enabled=True,
    )
    result = shreckllm.generate_grounded_answer(
        query="Explain", evidence=[{"citation_id": "source-1"}], model_target=target,
        response_detail="detailed",
    )
    client = clients[0]
    assert result == '{"status":"unsupported"}'
    assert client.headers == {"Authorization": "Bearer internal-token"}
    assert client.payload["provider_id"] == "anthropic"
    assert client.payload["model"] == "claude-sonnet-4"
    assert client.payload["reasoning"] is True
    assert client.payload["reasoning_effort"] == "high"
    assert client.payload["web_search_enabled"] is True
    assert client.payload["max_tokens"] == 4000
    assert client.payload["response_format"] == shreckllm.GROUNDING_RESPONSE_FORMAT
    assert "Requested response detail: detailed" in client.payload["messages"][1]["content"]
