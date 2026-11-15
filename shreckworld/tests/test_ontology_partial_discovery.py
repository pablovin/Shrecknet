"""Partial ontology discovery results are kept and malformed parts are exposed."""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnWindow
from app.db.session import create_schema
from app.services.ontology_compact import cache_fingerprint, model_window
from app.tasks import architect_learn_ontology as task


def test_window_keeps_valid_parts_logs_invalid_parts_and_does_not_split(tmp_path, monkeypatch):
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
            contract_version="ontology-schema-v5", status="pending",
        )
        db.add(checkpoint)
        db.commit()

        calls = []
        body = ('{"e":[{"n":"Investigator","d":"","b":[0],"p":['
                '{"n":"broken","t":"nope","s":"","d":"","m":[],"i":[],"b":[0]},'
                '{"n":"spot_hidden","t":"i","s":"Skill","d":"","m":[],"i":[],"b":[0]}],"r":[]}]}')

        def response(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=body, finish_reason="stop", input_tokens=12, output_tokens=7)

        monkeypatch.setattr(task, "generate_shreckworld_structured_output", response)
        monkeypatch.setattr(task, "model", lambda *_: "dummy")
        stats = {}
        proposals = task._window_extract(
            db, run, book, doc, batch, content, lookup, checkpoint, stats
        )

        assert len(calls) == 1
        assert {proposal["name"] for proposal in proposals} == {"Investigator", "spot_hidden"}
        assert checkpoint.status == "completed"
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
