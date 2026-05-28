from shrecknet_client.models import NovelistRunRead, Ontology, World


def test_world_model_parse() -> None:
    world = World.model_validate({"id": "w1", "name": "Earth", "ontology_ids": [1, 2]})
    assert world.id == "w1"
    assert world.ontology_ids == [1, 2]


def test_ontology_model_parse() -> None:
    ontology = Ontology.model_validate(
        {
            "id": 1,
            "name": "Test",
            "description": "d",
            "image_url": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
        }
    )
    assert ontology.id == 1
    assert ontology.name == "Test"


def test_novelist_run_model_parse() -> None:
    run = NovelistRunRead.model_validate(
        {
            "id": "run-1",
            "agent_id": "agent-1",
            "status": "done",
            "stage": "complete",
            "draft_text": "Draft body",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    assert run.id == "run-1"
    assert run.agent_id == "agent-1"
    assert run.draft_text == "Draft body"
