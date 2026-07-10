from shrecknet_client.models import AgentCreate, AgentRead, AgentUpdate, LLMReadinessReport, ProviderStatus


def test_agent_models_parse() -> None:
    payload = {
        "id": "a1",
        "name": "Agent",
        "job": "elder",
        "active": True,
        "avatar_url": None,
        "description": "d",
        "writing_style": "clear",
        "ontology_ids": [1],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    parsed = AgentRead.model_validate(payload)
    assert parsed.id == "a1"

    create = AgentCreate(name="x", job="elder", ontology_ids=[])
    assert create.active is True

    update = AgentUpdate(active=False)
    assert update.active is False


def test_readiness_report_model() -> None:
    report = LLMReadinessReport(
        checks={"shreckllm_reachable": True, "shreckllm_operational": False},
        providers=[ProviderStatus(provider_id="openai", enabled=False, valid=False, models=[])],
        ready=False,
        reasons=["No provider ready"],
    )
    assert report.ready is False
    assert report.providers[0].provider_id == "openai"
