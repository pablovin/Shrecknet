import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.services.architect_service import resolve_world_source_destination


def setup_source(monkeypatch, *, destinations=(), legacy=None):
    source = SimpleNamespace(title="The Vampire Sol", legacy_instance_id=legacy)
    load = Mock(return_value=(source, "A new narrative."))
    monkeypatch.setattr("app.services.architect_source_documents.load_architect_source", load)
    result = SimpleNamespace(data=AsyncMock(return_value=[{"instance_id": item} for item in destinations]))
    instances = SimpleNamespace(
        graph_session=SimpleNamespace(run=AsyncMock(return_value=result)),
        get_instance=AsyncMock(return_value=SimpleNamespace(instance_id=legacy, ontology_id=42)),
        create_instance=AsyncMock(return_value=SimpleNamespace(instance_id="new-page", ontology_id=42)),
    )
    return source, load, instances


def test_new_source_creates_stable_empty_destination(monkeypatch):
    source, load, instances = setup_source(monkeypatch)
    assert asyncio.run(resolve_world_source_destination(42, "source-1", "section-1", instances)) == "new-page"
    first = instances.create_instance.call_args
    source.title = "Renamed story"
    assert asyncio.run(resolve_world_source_destination(42, "source-1", "section-2", instances)) == "new-page"
    second = instances.create_instance.call_args
    assert first.kwargs["creation_key"] == second.kwargs["creation_key"] == "architect-source:42:source-1"
    assert first.kwargs["creation_fingerprint"] == second.kwargs["creation_fingerprint"]
    assert first.args[0].entities == []
    assert first.args[0].ontology_id == 42
    assert first.kwargs["trigger_background_jobs"] is False
    load.assert_called_with(42, "source-1", "section-2")


def test_existing_scene_destination_is_reused(monkeypatch):
    _, _, instances = setup_source(monkeypatch, destinations=("existing-page",), legacy="old-page")
    assert asyncio.run(resolve_world_source_destination(42, "source-1", None, instances)) == "existing-page"
    instances.create_instance.assert_not_called()


def test_legacy_destination_reused_only_in_same_world(monkeypatch):
    _, _, instances = setup_source(monkeypatch, legacy="old-page")
    assert asyncio.run(resolve_world_source_destination(42, "source-1", None, instances)) == "old-page"
    instances.get_instance.return_value = SimpleNamespace(instance_id="old-page", ontology_id=99)
    assert asyncio.run(resolve_world_source_destination(42, "source-1", None, instances)) == "new-page"


def test_ambiguous_destinations_require_explicit_selection(monkeypatch):
    _, _, instances = setup_source(monkeypatch, destinations=("a", "b"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(resolve_world_source_destination(42, "source-1", None, instances))
    assert exc.value.status_code == 409
    instances.create_instance.assert_not_called()


def test_invalid_source_never_creates_graph_state(monkeypatch):
    _, load, instances = setup_source(monkeypatch)
    load.side_effect = ValueError("Selected narrative Source has no text")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(resolve_world_source_destination(42, "source-1", None, instances))
    assert exc.value.status_code == 422
    instances.graph_session.run.assert_not_called()
    instances.create_instance.assert_not_called()
