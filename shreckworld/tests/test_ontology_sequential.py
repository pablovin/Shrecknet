"""V6 chapter history, canonical identity, coverage and failure regression tests."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.ontology_compact import CompactContractError, parse_discovery
from app.services.ontology_sequential import (
    STAGES, discover_chapter, reconcile, decode_stage, DUPLICATE_SCHEMA,
)
from app.services.ontology_discovery_batches import build_discovery_batches
from test_ontology_discovery_batches import make_map, block


LOOKUP = {i: {"block_id": f"block-{i}", "section_id": "section", "fragment_index": 0}
          for i in range(6)}
BASE = {"chapter_id": "chapter", "source_id": "book"}
CONTENT = json.dumps({"source": "Book", "chapter": "Chapter", "s": [
    {"h": "Chapter", "b": [{"i": i, "x": f"Entire passage {i}"} for i in LOOKUP]}]})


def entity(name):
    return {"kind": "entity", "name": name, "description": name, "evidence": []}


def prop(name):
    return {"n": name, "t": "i", "s": "Skill", "d": "Description",
            "m": [], "i": [], "b": [0]}


def reply(data, finish="stop"):
    return SimpleNamespace(text=json.dumps(data), finish_reason=finish,
                           input_tokens=10, output_tokens=20)


def test_four_stages_preserve_exact_history_and_canonical_registry():
    calls, checkpoints = [], []
    bodies = {
        "entities": {"e": [{"n": "Investigator", "d": "An actor", "b": [0]}]},
        "properties": {"e": [{"n": "Investigator", "p": [prop("spot_hidden")]}]},
        "relationships": {"e": [{"n": "Investigator", "r": [
            {"n": "OWNS", "t": "Item", "d": "Ownership", "b": [1]}]}]},
        "validation": {"e": [], "issues": []},
    }
    def ask(stage, prompt, content, schema, messages):
        calls.append((stage, deepcopy(messages)))
        return reply(bodies[stage])
    result = discover_chapter(CONTENT, LOOKUP, BASE, [entity("Item")], ask,
                              lambda rows, stage: checkpoints.append(stage), {})
    assert checkpoints == ["entities", "properties", "relationships", "validation"]
    for index, (_, history) in enumerate(calls):
        assert len(history) == 3 + 2 * index
        if index:
            assert history[:-2] == calls[index - 1][1]
            assert history[-2]["content"] == json.dumps(bodies[calls[index - 1][0]])
        assert "Entire passage 5" in history[1]["content"]
    assert "spot_hidden" in calls[2][1][-1]["content"]
    assert {p["name"] for p in result} == {"Investigator", "spot_hidden", "OWNS"}
    assert next(p for p in result if p["kind"] == "relationship")["target"] == "Item"


def test_second_chapter_adds_missing_properties_without_rediscovering_owner():
    bodies = iter([{"e": []}, {"e": [{"n": "Investigator", "p": [prop("dodge")]}]},
                   {"e": []}, {"e": [], "issues": []}])
    existing = [entity("Investigator")]
    result = discover_chapter(CONTENT, LOOKUP, BASE, existing,
        lambda *_: reply(next(bodies)), lambda *_: None, {})
    assert [(p["kind"], p["name"]) for p in result] == [("property", "dodge")]
    assert existing == [entity("Investigator")]


def test_normalized_identity_and_ambiguity_remap_edges_without_editing_known():
    known = [entity("Investigator")]
    original = deepcopy(known)
    proposals = [entity("Investigators"), entity("Item"),
        {"kind": "relationship", "name": "OWNS", "source": "Investigators",
         "target": "Item", "description": "", "evidence": [{"block_ids": ["1"]}]}]
    calls = []
    decisions = {}
    def ask(*args):
        calls.append(args)
        return reply({"same": [True]})
    result = reconcile(proposals, known, ask, decisions)
    assert known == original
    assert {p["name"] for p in result} == {"Item", "OWNS"}
    assert result[1]["source"] == "Investigator"
    reconcile(proposals, known, ask, decisions)
    assert len(calls) == 1


def test_ambiguous_false_and_incompatible_properties_are_not_merged():
    def ask(*_):
        return reply({"same": [False]})
    assert len(reconcile([entity("Character"), entity("Characters")], [], ask)) == 2
    a = {"kind": "property", "entity": "Actor", "name": "age", "data_type": "integer"}
    b = {**a, "data_type": "text"}
    result = reconcile([b], [a], ask)
    assert result[0]["conflict"]["reason"] == "incompatible_property_definitions"


@pytest.mark.parametrize("finish", ["length", "max_tokens", "max_output_tokens", "refusal"])
def test_truncated_or_refused_stage_never_completes(finish):
    checkpoints = []
    with pytest.raises(CompactContractError):
        discover_chapter(CONTENT, LOOKUP, BASE, [],
            lambda *_: reply({"e": []}, finish), lambda *_: checkpoints.append(1), {})
    assert not checkpoints


def test_invalid_citations_unknown_endpoints_and_validation_issues_fail():
    with pytest.raises(CompactContractError):
        decode_stage("entities", {"e": [{"n": "Actor", "d": "", "b": [99]}]},
                     LOOKUP, BASE, [], {})
    with pytest.raises(CompactContractError):
        decode_stage("relationships", {"e": [{"n": "Actor", "r": [
            {"n": "OWNS", "t": "Missing", "d": "", "b": [0]}]}]},
                     LOOKUP, BASE, [entity("Actor")], {})
    stats = {}
    with pytest.raises(CompactContractError, match="source_validation_failed"):
        decode_stage("validation", {"e": [], "issues": ["Unsupported age"]},
                     LOOKUP, BASE, [], stats)
    assert stats["malformed_parts"][0]["message"] == "Unsupported age"


def test_no_source_schema_or_parser_quotas():
    text = "full source " * 20000
    batches = build_discovery_batches(make_map(), {"b1": block("b1", text, 1)})
    assert len(batches) == 1
    assert batches[0]["sections"][0]["blocks"][0]["text"] == text
    for _, _, schema in STAGES:
        assert "maxItems" not in json.dumps(schema)
        assert "maxLength" not in json.dumps(schema)
    assert "maxItems" not in json.dumps(DUPLICATE_SCHEMA)
    properties = [prop(f"skill_{i}") for i in range(90)]
    properties[0]["d"] = "long description " * 100
    properties[0]["b"] = list(LOOKUP)
    properties[0]["m"] = [{"n": f"key{i}", "t": "s", "v": "value " * 100}
                          for i in range(20)]
    payload = {"e": [{"n": f"Type{i}", "d": "", "b": [0], "p": properties, "r": []}
                     for i in range(30)]}
    stats = {}
    result = parse_discovery(payload, LOOKUP, BASE, stats=stats)
    assert len(result) == 30 * 91
    assert not stats["malformed_parts"]
    assert len(next(p for p in result if p["kind"] == "property")["definition"]) == 20


def test_reconciliation_preserves_all_evidence():
    a, b = entity("Actor"), entity("actor")
    a["evidence"], b["evidence"] = [{"block_ids": ["a"]}], [{"block_ids": ["b"]}]
    result = reconcile([a, b], [], lambda *_: pytest.fail("exact match needs no model"))
    assert len(result) == 1
    assert result[0]["evidence"] == [{"block_ids": ["a"]}, {"block_ids": ["b"]}]


def test_real_task_runs_two_chapters_in_order_and_reuses_canonical_types(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow, OntologyLearnChapter
    from app.tasks import architect_learn_ontology as task
    from app.services.ontology_compact import CONTRACT_VERSION
    engine = create_engine("sqlite:///" + str(tmp_path / "run.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = OntologyLearnRun(ontology_id=9, owner_id="owner", idempotency_key="two",
            input_sources=[], base_revision=0,
            model_targets={"discovery": {"provider": "openrouter", "name": "dummy"},
                           "reconciliation": {"provider": "openrouter", "name": "dummy"}})
        db.add(run)
        db.commit()
        run_id = run.id
    monkeypatch.setattr(task, "session_factory", lambda: lambda: Session(engine))
    monkeypatch.setattr(task.ontology_studio, "graph", lambda *_: {
        "entities": [], "properties": [], "relationships": []})
    def prepare(db, run):
        planned = []
        for chapter in ("first", "second"):
            row = OntologyLearnWindow(run_id=run.id, ontology_id=9, source_id="source",
                document_id="doc", content_digest="digest", chapter_id=chapter,
                chapter_title=chapter, window_index=1, fingerprint=chapter,
                contract_version=CONTRACT_VERSION, status="pending")
            db.add(row)
            planned.append((SimpleNamespace(id="source", title="Book"),
                SimpleNamespace(id="doc", source_sha256="digest"),
                {"chapter_id": chapter, "chapter_title": chapter}, CONTENT, LOOKUP, row))
        db.commit()
        return planned
    monkeypatch.setattr(task, "_prepare", prepare)
    calls = []
    def generate(**kwargs):
        stage = kwargs["schema_name"].rsplit("_", 1)[-1]
        calls.append(deepcopy(kwargs))
        assert kwargs["max_tokens"] is None
        if len(calls) <= 4:
            data = {"e": [{"n": "Investigator", "d": "", "b": [0]}]} if stage == "entities" else {"e": []}
        else:
            assert "Investigator" in kwargs["messages"][1]["content"]
            data = {"e": [{"n": "Investigator", "p": [prop("dodge")]}]} if stage == "properties" else {"e": []}
        if stage == "validation":
            data["issues"] = []
        return reply(data)
    monkeypatch.setattr(task, "generate_shreckworld_structured_output", generate)
    task.run_ontology_discovery(run_id)
    with Session(engine) as db:
        saved = db.get(OntologyLearnRun, run_id)
        assert saved.status == "awaiting_review"
        assert saved.llm_calls == 8
        assert saved.input_tokens == 80 and saved.output_tokens == 160
        assert {p["name"] for p in saved.proposals} == {"Investigator", "dodge"}
        assert len(list(db.scalars(select(OntologyLearnChapter)))) == 2
        assert all(row.status == "completed" for row in db.scalars(select(OntologyLearnWindow)))
        assert all(row.input_tokens == 40 for row in db.scalars(select(OntologyLearnWindow)))
    # A duplicate delivery must not execute the claimed job twice.
    task.run_ontology_discovery(run_id)
    assert len(calls) == 8
    engine.dispose()


def test_checkpoint_cache_depends_on_prior_registry_and_cancellation_is_terminal(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.db.base import Base
    from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
    from app.tasks import architect_learn_ontology as task
    from app.services.ontology_compact import CONTRACT_VERSION
    from app.services.ontology_sequential import DiscoveryCancelled
    engine = create_engine("sqlite:///" + str(tmp_path / "cache.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = OntologyLearnRun(ontology_id=9, owner_id="owner", idempotency_key="cache",
            input_sources=[], base_revision=0, status="running",
            model_targets={"discovery": {"provider": "openrouter", "name": "dummy"}})
        db.add(run)
        db.commit()
        calls = []
        def generate(**kwargs):
            calls.append(kwargs)
            return reply({"e": [], **({"issues": []} if kwargs["schema_name"].endswith("validation") else {})})
        monkeypatch.setattr(task, "generate_shreckworld_structured_output", generate)
        checkpoint_number = 0
        def checkpoint():
            nonlocal checkpoint_number
            checkpoint_number += 1
            row = OntologyLearnWindow(run_id=run.id, ontology_id=9, source_id="source",
                document_id="doc", content_digest="digest", chapter_id="chapter",
                chapter_title="Chapter", window_index=checkpoint_number, fingerprint="pending",
                contract_version=CONTRACT_VERSION, status="pending")
            db.add(row)
            db.commit()
            return row
        def extract(row, known):
            return task._window_extract(db, run, SimpleNamespace(id="source", title="Book"),
                SimpleNamespace(id="doc", source_sha256="digest"),
                {"chapter_id": "chapter", "chapter_title": "Chapter"},
                CONTENT, LOOKUP, row, {}, known)
        first = checkpoint()
        extract(first, [])
        cached = checkpoint()
        extract(cached, [])
        assert len(calls) == 4 and cached.cached
        changed = checkpoint()
        extract(changed, [entity("Item")])
        assert len(calls) == 8 and not changed.cached
        assert changed.fingerprint != first.fingerprint
        cancelled = checkpoint()
        run.status = "cancel_requested"
        db.commit()
        with pytest.raises(DiscoveryCancelled):
            extract(cancelled, [entity("Different")])
        assert run.status == "cancelled" and cancelled.status == "cancelled"
        assert len(calls) == 8
    engine.dispose()


def test_adapter_sends_explicit_history_without_token_override(monkeypatch):
    from app.integrations import shreckllm
    from app.schemas.shreckworld import ShreckWorldModelTarget
    sent = []
    class Response:
        def __init__(self, body):
            self.body = body
        def raise_for_status(self):
            pass
        def json(self):
            return self.body
    class Client:
        def __init__(self, **_):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def post(self, url, json, **_):
            sent.append(deepcopy(json))
            return Response({"job_id": "job"})
        def get(self, url, **_):
            return Response({"text": '{"e":[]}', "finish_reason": "stop"} if url.endswith("/result")
                            else {"status": "succeeded"})
    monkeypatch.setattr(shreckllm.httpx, "Client", Client)
    monkeypatch.setattr(shreckllm, "get_settings", lambda: SimpleNamespace(
        synthesis_timeout_seconds=60, shreckllm_base_url="http://llm", synthesis_poll_interval_seconds=0))
    history = [{"role": "system", "content": "system"}, {"role": "user", "content": CONTENT},
               {"role": "assistant", "content": '{"e":[]}'}, {"role": "user", "content": "next stage"}]
    shreckllm.generate_shreckworld_structured_output(
        usage_tag="test", system_prompt="system", user_content="next stage", schema_name="test",
        response_schema=STAGES[0][2], model_target=ShreckWorldModelTarget(provider="openrouter", name="dummy"),
        messages=history)
    assert sent[0]["messages"] == history
    assert "max_tokens" not in sent[0]
    assert sent[0]["use_conversation_memory"] is False


def test_many_ambiguous_pairs_use_one_boolean_batch_without_quota():
    proposals = [entity(f"Character Type {i}") for i in range(30)]
    calls = []
    def ask(stage, prompt, content, schema, history):
        data = json.loads(content)
        calls.append(data)
        return reply({"same": [False for _ in data["pairs"]]})
    result = reconcile(proposals, [], ask)
    assert len(result) == 30
    assert len(calls) == 1
    assert len(calls[0]["pairs"]) > 24
    assert len(calls[0]["c"]) == 30


def test_request_and_property_validation_have_no_old_size_quotas():
    from app.api.routers.ontology_studio import LearnRequest, EntityCreate
    from app.services.ontology_studio import validate_property_spec
    request = LearnRequest(idempotency_key="unlimited", source_ids=[str(i) for i in range(30)])
    assert len(request.source_ids) == 30
    assert len(EntityCreate(expected_revision=0, name="N" * 500).name) == 500
    # Definition text and free-form semantics must reach persistence intact.
    validate_property_spec({"data_type": "text", "semantic_type": "Semantic " * 100})
