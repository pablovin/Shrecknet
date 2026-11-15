"""Adaptive one-pass ontology discovery overflow recovery and provenance tests."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
from app.services.ontology_compact import (
    CompactContractError, cache_fingerprint, model_window, proposal_counts,
    LEGACY_DISCOVERY_PROMPT, DISCOVERY_PROMPT,
)
from app.services.ontology_discovery_batches import split_discovery_batch
from app.tasks import architect_learn_ontology as task


def section(name: str, blocks: list[tuple[str, str]]):
    return {
        "section_id": "section-" + name, "heading_path": ["Chapter 4", name],
        "pages": {}, "section_type": "section",
        "blocks": [
            {"id": identifier, "type": "paragraph", "text": text, "fragment_index": 0}
            for identifier, text in blocks
        ],
    }


def batch(sections):
    return {"chapter_id": "ch4", "chapter_title": "Skills", "sections": sections}


def flatten(batch_data):
    return "".join(block["text"] for sec in batch_data["sections"]
                   for block in sec["blocks"])


@pytest.mark.parametrize("sections", [
    [section("A", [("a", "a" * 170)]), section("B", [("b", "b" * 190)])],
    [section("A", [("a", "a" * 170), ("b", "b" * 190)])],
    [section("A", [("a", "a" * 360)])],
])
def test_splitting_is_nonlossy_and_preserves_original_provenance(sections):
    original = batch(sections)
    first, second = split_discovery_batch(original)
    assert flatten(first) + flatten(second) == flatten(original)
    assert first["chapter_id"] == second["chapter_id"] == "ch4"
    ids = lambda value: [b["id"] for sec in value["sections"] for b in sec["blocks"]]
    assert set(ids(first) + ids(second)) == set(ids(original))
    if len(sections) == 1 and len(sections[0]["blocks"]) == 1:
        left = first["sections"][0]["blocks"][0]
        right = second["sections"][0]["blocks"][0]
        assert left["fragment_char_offset"] == 0
        assert right["fragment_char_offset"] == len(left["text"])
        _, lookup = model_window(second, "Rulebook")
        assert lookup[0]["char_start"] == len(left["text"])
        assert lookup[0]["char_end"] == 360


def test_too_small_window_cannot_split_further():
    with pytest.raises(ValueError, match="discovery_fragment_too_small_to_split"):
        split_discovery_batch(batch([section("A", [("a", "tiny")])]))


@pytest.fixture
def world(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "adaptive.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        target = {"provider": "openrouter", "name": "dummy"}
        run = OntologyLearnRun(
            ontology_id=9, owner_id="owner", idempotency_key="adaptive-test",
            input_sources=[], base_revision=0, model_targets={"discovery": target},
        )
        db.add(run)
        db.flush()
        book = SimpleNamespace(id="source", title="Example RPG")
        doc = SimpleNamespace(id="doc", source_sha256="digest")
        original = batch([section("skills", [
            ("b1", "First rulebook skill. " * 16),
            ("b2", "Second rulebook skill. " * 16),
            ("b3", "Third rulebook skill. " * 16),
        ])])
        text, lookup = model_window(original, book.title)
        checkpoint = OntologyLearnWindow(
            run_id=run.id, ontology_id=9, source_id=book.id, document_id=doc.id,
            content_digest=doc.source_sha256, chapter_id=original["chapter_id"],
            chapter_title=original["chapter_title"], window_index=1,
            fingerprint=cache_fingerprint(text, target), contract_version="ontology-schema-v4",
            status="pending",
        )
        db.add(checkpoint)
        db.commit()
        yield db, run, book, doc, original, text, lookup, checkpoint
    engine.dispose()


def test_adaptive_overflow_short_circuits_and_merges_children(world, monkeypatch):
    db, run, book, doc, original, text, lookup, checkpoint = world
    calls = []

    def model_response(**kwargs):
        payload = json.loads(kwargs["user_content"])
        fragments = [b for s in payload["s"] for b in s["b"]]
        body = "".join(b["x"] for b in fragments)
        if len(body) > 400:
            data = {"e": [], "overflow": True}
        else:
            props = [{
                "n": "skill_" + frag["x"].split()[0].lower() + "_" + str(i),
                "t": "i", "s": "Skill", "d": "Rulebook skill", "m": [], "i": [],
                "b": [i],
            } for i, _ in enumerate(fragments)]
            data = {"e": [{
                "n": "Investigator", "d": "Player actor", "b": [0], "p": props, "r": [],
            }], "overflow": False}
        calls.append((len(body), data))
        return SimpleNamespace(
            text=json.dumps(data), finish_reason="stop",
            input_tokens=10, output_tokens=5,
        )

    monkeypatch.setattr(task, "generate_shreckworld_structured_output", model_response)
    monkeypatch.setattr(task, "model", lambda *_: "dummy")
    stats = {}
    result = task._window_extract(
        db, run, book, doc, original, text, lookup, checkpoint, stats
    )
    assert calls[0][1] == {"e": [], "overflow": True}
    assert len(calls) >= 3
    assert stats["adaptive_splits"] >= 1
    assert stats["overflow_calls"] >= 1
    assert stats["split_child_windows"] >= 2
    assert stats["cached_child_windows"] == 0
    assert stats["properties"] >= 2
    assert proposal_counts(result)["entity_types"] == 1
    assert run.llm_calls == len(calls)
    rows = list(db.scalars(select(OntologyLearnWindow)
                           .where(OntologyLearnWindow.run_id == run.id)))
    assert len(rows) == 1 + stats["split_child_windows"]
    assert all(row.status == "completed" for row in rows)
    assert len({row.window_index for row in rows}) == len(rows)
    assert checkpoint.proposals == result
    property_sources = {block for proposal in result if proposal["kind"] == "property"
                        for evidence in proposal["evidence"]
                        for block in evidence["block_ids"]}
    assert property_sources == {"b1", "b2", "b3"}

    # Recreating the same window in a later run costs no extra model request.
    rerun = OntologyLearnRun(
        ontology_id=9, owner_id="owner", idempotency_key="adaptive-rerun",
        input_sources=[], base_revision=0, model_targets=run.model_targets,
    )
    db.add(rerun)
    db.flush()
    retry = OntologyLearnWindow(
        run_id=rerun.id, ontology_id=9, source_id=book.id, document_id=doc.id,
        content_digest=doc.source_sha256, chapter_id=original["chapter_id"],
        chapter_title=original["chapter_title"], window_index=1,
        fingerprint=cache_fingerprint(text, run.model_targets["discovery"]),
        contract_version="ontology-schema-v4", status="pending",
    )
    db.add(retry)
    db.commit()
    cached_stats = {}
    assert task._window_extract(
        db, rerun, book, doc, original, text, lookup, retry, cached_stats
    ) == result
    assert cached_stats["cached"] is True
    assert len(calls) == run.llm_calls


def test_split_limit_reports_actionable_error(world, monkeypatch):
    db, run, book, doc, original, text, lookup, checkpoint = world
    monkeypatch.setattr(task, "generate_shreckworld_structured_output",
                        lambda **_: SimpleNamespace(
                            text=json.dumps({"e": [], "overflow": True}),
                            finish_reason="stop", input_tokens=10, output_tokens=5,
                        ))
    monkeypatch.setattr(task, "model", lambda *_: "dummy")
    monkeypatch.setattr(task, "MAX_ADAPTIVE_SPLIT_DEPTH", 1)
    with pytest.raises(CompactContractError) as failure:
        task._window_extract(db, run, book, doc, original, text, lookup, checkpoint, {})
    assert failure.value.code == "compact_overflow_split_limit"
    assert db.get(OntologyLearnWindow, checkpoint.id).error_code == "compact_overflow_split_limit"


def test_compatible_legacy_v4_fingerprint_is_still_reusable(world, monkeypatch):
    db, run, book, doc, original, text, lookup, checkpoint = world
    assert LEGACY_DISCOVERY_PROMPT != DISCOVERY_PROMPT
    legacy_hash = cache_fingerprint(text, run.model_targets["discovery"],
                                    prompt=LEGACY_DISCOVERY_PROMPT)
    row = OntologyLearnWindow(
        run_id=run.id, ontology_id=9, source_id=book.id, document_id=doc.id,
        content_digest=doc.source_sha256, chapter_id=original["chapter_id"],
        chapter_title=original["chapter_title"], window_index=2,
        fingerprint=legacy_hash, contract_version="ontology-schema-v4",
        status="completed", proposals=[{"kind": "entity", "name": "Investigator", "evidence": []}],
    )
    db.add(row)
    db.commit()
    monkeypatch.setattr(task, "generate_shreckworld_structured_output",
                        lambda **_: pytest.fail("Successful legacy V4 window must be cached"))
    assert task._window_extract(
        db, run, book, doc, original, text, lookup, checkpoint, {}
    ) == row.proposals
