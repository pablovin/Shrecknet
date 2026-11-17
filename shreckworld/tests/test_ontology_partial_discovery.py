"""Partial ontology discovery results are kept and malformed parts are exposed."""
from __future__ import annotations

from types import SimpleNamespace
import json
import pytest
from app.services.ontology_compact import CONTRACT_VERSION, CompactContractError

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
from app.db.session import create_schema
from app.services.ontology_compact import cache_fingerprint, model_window
from app.tasks import architect_learn_ontology as task


def test_window_keeps_prior_stages_but_invalid_properties_fail(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "partial.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        target = {"provider": "openrouter", "name": "dummy"}
        run = OntologyLearnRun(
            ontology_id=9, owner_id="owner", idempotency_key="partial-test",
            input_sources=[], base_revision=0, model_targets={"discovery": target},
        )
        db.add(run)
        db.flush()
        book = SimpleNamespace(id="source", title="Example RPG")
        doc = SimpleNamespace(id="doc", source_sha256="digest")
        batch = {"chapter_id": "ch4", "chapter_title": "Skills", "sections": [{
            "section_id": "s1", "heading_path": ["Skills"],
            "blocks": [{"id": "b1", "text": "Skill rule", "fragment_index": 0}],
        }]}
        content, lookup = model_window(batch, book.title)
        checkpoint = OntologyLearnWindow(
            run_id=run.id, ontology_id=9, source_id=book.id, document_id=doc.id,
            content_digest=doc.source_sha256, chapter_id=batch["chapter_id"],
            chapter_title=batch["chapter_title"], window_index=1,
            fingerprint=cache_fingerprint(content, target),
            contract_version=CONTRACT_VERSION, status="pending",
        )
        db.add(checkpoint)
        db.commit()

        calls = []
        body = ('{"e":[{"n":"Investigator","d":"","b":[0],"p":['
                '{"n":"broken","t":"nope","s":"","d":"","m":[],"i":[],"b":[0]},'
                '{"n":"spot_hidden","t":"i","s":"Skill","d":"","m":[],"i":[],"b":[0]}],"r":[]}]}')

        def response(**kwargs):
            calls.append(kwargs)
            stage = kwargs["schema_name"].rsplit("_", 1)[-1]
            data = ({"e": [{"n": "Investigator", "d": "", "b": [0]}]} if stage == "entities"
                    else {"e": [{"n": "Investigator", "p": json.loads(body)["e"][0]["p"]}]})
            return SimpleNamespace(text=json.dumps(data), finish_reason="stop", input_tokens=12, output_tokens=7)

        monkeypatch.setattr(task, "generate_shreckworld_structured_output", response)
        monkeypatch.setattr(task, "model", lambda *_: "dummy")
        stats = {}
        with pytest.raises(CompactContractError, match="properties_invalid_definitions"):
            task._window_extract(db, run, book, doc, batch, content, lookup, checkpoint, stats)
        assert len(calls) == 2
        assert {proposal["name"] for proposal in checkpoint.proposals} == {"Investigator"}
        assert checkpoint.status == "failed"
        assert checkpoint.error_code == "properties_invalid_definitions"
        assert checkpoint.validation_diagnostics == [{
            "path": "e[0].p[0]", "code": "invalid_compact_type", "name": "broken",
        }]
        assert len(list(db.scalars(select(OntologyLearnWindow)))) == 1
        from app.api.routers import ontology_studio
        monkeypatch.setattr(ontology_studio, "auth", lambda *_: None)
        windows = ontology_studio.list_run_windows(9, run.id, "token", None, db)
        assert windows[0]["validation_diagnostics"] == checkpoint.validation_diagnostics
    engine.dispose()


def test_existing_window_table_gets_diagnostics_column(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "migration.db"))
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE shreckworld_ontology_learn_windows DROP COLUMN validation_diagnostics"
        )
    monkeypatch.setattr("app.db.session._engine", lambda: engine)

    create_schema()

    columns = {column["name"] for column in inspect(engine).get_columns(
        "shreckworld_ontology_learn_windows")}
    assert "validation_diagnostics" in columns
    engine.dispose()


def test_reconciliation_parses_model_response_after_discovery(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "reconciliation.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = OntologyLearnRun(
            ontology_id=9, owner_id="owner", idempotency_key="reconcile-test",
            input_sources=[], base_revision=0,
            model_targets={"reconciliation": {"provider": "openrouter", "name": "dummy"}},
        )
        db.add(run)
        db.commit()
        monkeypatch.setattr(task.ontology_studio, "graph", lambda *_: {
            "entities": [], "properties": [], "relationships": [],
        })
        monkeypatch.setattr(task, "model", lambda *_: "dummy")
        monkeypatch.setattr(task, "generate_shreckworld_structured_output", lambda **_: SimpleNamespace(
            text='{"same":[true]}', finish_reason="stop",
            input_tokens=12, output_tokens=7,
        ))
        candidates = {
            "one": {"kind": "entity", "name": "Investigator", "description": "", "evidence": []},
            "two": {"kind": "entity", "name": "Investigators", "description": "", "evidence": []},
        }

        result = task._reconcile(db, run, candidates)

        assert len(result) == 1
        assert result[0]["name"] == "Investigator"
        assert run.llm_calls == 1
