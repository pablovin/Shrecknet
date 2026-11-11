from __future__ import annotations

from test_upload_metadata_api import api


def identity(monkeypatch):
    def read(path, _token):
        if path.startswith("/ontologies/"):
            value = path.rsplit("/", 1)[-1]
            return {"id": int(value), "name": f"Ontology {value}", "description": None}
        if path.startswith("/contracts/users/"):
            value = path.rsplit("/", 1)[-1]
            return {"id": value, "role": "player", "full_name": "Player", "email": "player@example.test"}
        if path.startswith("/character-agents/"):
            return {"id": "agent-1", "ontology_id": 7, "created_by_user_id": 2}
        raise AssertionError(path)
    monkeypatch.setattr("app.integrations.ontologies.shrecknet_read", read)
    monkeypatch.setattr("app.api.routers.game_engine.shrecknet_read", read)


def prepare_published(client, headers):
    ruleset = client.post("/api/v1/game-engine/rulesets", headers=headers,
                          json={"name": "Pendragon", "edition": "6e"})
    assert ruleset.status_code == 201, ruleset.text
    ruleset_id = ruleset.json()["id"]
    binding = client.post("/api/v1/game-engine/bindings", headers=headers,
                          json={"ontology_id": 7, "ruleset_id": ruleset_id})
    assert binding.status_code == 201, binding.text
    definition = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/definitions?ontology_id=7", headers=headers,
                             json={"kind": "entity_schema", "schema_version": "1.0",
                                   "payload": {"fields": [{"name": "traits", "type": "object"}]},
                                   "provenance": []})
    assert definition.status_code == 201, definition.text
    revision = definition.json()
    from app.db.session import session_factory
    from app.models.game_engine import GameRuleset
    with session_factory()() as db:
        expected_revision = db.get(GameRuleset, ruleset_id).definition_revision
    approved = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/approve?expected_revision={expected_revision}", headers=headers)
    assert approved.status_code == 200, approved.text
    published = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/publish", headers=headers,
                            json={"expected_revision": expected_revision})
    assert published.status_code == 201, published.text
    return ruleset_id, binding.json(), published.json()


def test_ruleset_binding_publication_and_table_lifecycle(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    _, binding, version = prepare_published(client, headers)
    assert client.get("/api/v1/game-engine/bindings?ontology_id=7", headers=headers).json()[0]["id"] == binding["id"]
    table = client.post("/api/v1/game-engine/tables", headers=headers,
                        json={"ontology_id": 7, "version_id": version["id"], "name": "Winter Court"})
    assert table.status_code == 201, table.text
    body = table.json()
    assert body["ontology_id"] == 7 and body["version_id"] == version["id"] and body["revision"] == 1
    added = client.put(f"/api/v1/game-engine/tables/{body['id']}/participants?ontology_id=7", headers=headers,
                       json={"user_id": "2", "role": "player", "character_agent_id": "agent-1"})
    assert added.status_code == 201, added.text
    assert added.json()["character_agent_id"] == "agent-1"
    stale = client.patch(f"/api/v1/game-engine/tables/{body['id']}?ontology_id=7", headers={**headers, "If-Match": '"0"'},
                         json={"name": "Renamed"})
    assert stale.status_code == 409
    update = client.patch(f"/api/v1/game-engine/tables/{body['id']}?ontology_id=7", headers={**headers, "If-Match": '"2"'},
                          json={"name": "Renamed"})
    assert update.status_code == 200 and update.json()["revision"] == 3
    removed = client.delete(f"/api/v1/game-engine/tables/{body['id']}/participants/2?ontology_id=7", headers=headers)
    assert removed.status_code == 204
    archived = client.delete(f"/api/v1/game-engine/tables/{body['id']}?ontology_id=7", headers=headers)
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    assert client.patch(f"/api/v1/game-engine/tables/{body['id']}?ontology_id=7", headers={**headers, "If-Match": '"3"'},
                        json={"name": "No"}).status_code == 410


def test_run_placeholder_is_idempotent_scoped_and_reports_no_extraction(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    ruleset_id, binding, _ = prepare_published(client, headers)
    monkeypatch.setattr("app.tasks.game_engine.run_generation_placeholder.delay", lambda run_id: None)
    payload = {"idempotency_key": "request-1", "binding_id": binding["id"], "source_refs": []}
    first = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/runs", headers=headers, json=payload)
    second = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/runs", headers=headers, json=payload)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert client.get(f"/api/v1/game-engine/runs/{first.json()['id']}", headers=headers).json()["status"] == "queued"
    changed = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/runs", headers=headers,
                          json={**payload, "source_refs": [{"source_id": "s1", "document_id": "d1",
                              "document_revision_id": "d1", "content_digest": "a" * 64,
                              "locator_kind": "physical_page", "locator": "2"}]})
    assert changed.status_code == 409
    from app.tasks.game_engine import run_generation_placeholder
    run_generation_placeholder.run(first.json()["id"])
    finished = client.get(f"/api/v1/game-engine/runs/{first.json()['id']}", headers=headers).json()
    assert finished["status"] == "succeeded"
    assert finished["result"] == {"execution_mode": "placeholder", "candidates_created": 0, "extraction_performed": False}
    assert finished["diagnostics"] == [{"code": "extraction_not_implemented", "retryable": False}]


def test_game_engine_rejects_unpublished_table_version_and_invalid_definition(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    ruleset = client.post("/api/v1/game-engine/rulesets", headers=headers,
                          json={"name": "Draft", "edition": "1"}).json()
    client.post("/api/v1/game-engine/bindings", headers=headers,
                json={"ontology_id": 7, "ruleset_id": ruleset["id"]})
    assert client.post("/api/v1/game-engine/tables", headers=headers,
                       json={"ontology_id": 7, "version_id": "missing", "name": "Table"}).status_code == 409
    assert client.post(f"/api/v1/game-engine/rulesets/{ruleset['id']}/definitions?ontology_id=7", headers=headers,
                       json={"kind": "dice", "schema_version": "1.0", "payload": {}}).status_code == 422
    assert client.post(f"/api/v1/game-engine/rulesets/{ruleset['id']}/approve?expected_revision=1", headers=headers).status_code == 422


def test_generation_source_reference_is_revision_and_ontology_scoped(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    ruleset_id, binding, _ = prepare_published(client, headers)
    monkeypatch.setattr("app.tasks.game_engine.run_generation_placeholder.delay", lambda _run_id: None)
    from app.db.session import session_factory
    from app.models import ShreckWorldDocument, ShreckWorldDocumentPage, ShreckWorldLibraryItem
    digest = "a" * 64
    with session_factory()() as db:
        item = ShreckWorldLibraryItem(ontology_id=7, title="Rules", pdf_path="/private/rules.pdf",
                                      source_sha256=digest, embedding_status="not_embedded")
        db.add(item)
        db.flush()
        db.add(ShreckWorldDocument(id="revision-1", library_item_id=item.id, is_active=True,
                                   source_sha256=digest, parser_name="test", parser_version="1",
                                   embedding_version="test", embedding_model="test",
                                   embedding_fingerprint="b" * 64, embedding_dimension=2,
                                   manifest_path="/private/manifest.json"))
        db.add(ShreckWorldDocumentPage(id="page-1", document_id="revision-1", page_number=12, page_label="xii"))
        db.commit()
        source_id = item.id
    ref = {"source_id": source_id, "document_id": "revision-1", "document_revision_id": "revision-1",
           "content_digest": digest, "locator_kind": "printed_page", "locator": "xii"}
    created = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/runs", headers=headers,
                          json={"idempotency_key": "source-run", "binding_id": binding["id"], "source_refs": [ref]})
    assert created.status_code == 202, created.text
    invalid = client.post(f"/api/v1/game-engine/rulesets/{ruleset_id}/runs", headers=headers,
                          json={"idempotency_key": "bad-source-run", "binding_id": binding["id"],
                                "source_refs": [{**ref, "content_digest": "c" * 64}]})
    assert invalid.status_code == 422


def test_game_engine_authorization_and_ontology_scoping(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    monkeypatch.setattr("app.integrations.ontologies.shrecknet_read",
                        lambda path, token: (_ for _ in ()).throw(__import__("fastapi").HTTPException(404, "missing")))
    ruleset = client.post("/api/v1/game-engine/rulesets", headers=headers,
                          json={"name": "Draft", "edition": "1"}).json()
    assert client.post("/api/v1/game-engine/bindings", headers=headers,
                       json={"ontology_id": 7, "ruleset_id": ruleset["id"]}).status_code == 404
    from app.main import app
    from app.api.deps import ShrecknetPrincipal, require_world_manager
    app.dependency_overrides[require_world_manager] = lambda: ShrecknetPrincipal(user_id="2", role="world_builder")
    assert client.post(f"/api/v1/game-engine/rulesets/{ruleset['id']}/approve?expected_revision=1", headers=headers).status_code == 403


def test_ruleset_rejection_records_reviewer_reason(api, monkeypatch):
    identity(monkeypatch)
    client, headers, _ = api
    ruleset = client.post("/api/v1/game-engine/rulesets", headers=headers,
                          json={"name": "Draft", "edition": "1"}).json()
    client.post(f"/api/v1/game-engine/bindings", headers=headers,
                json={"ontology_id": 7, "ruleset_id": ruleset["id"]})
    client.post(f"/api/v1/game-engine/rulesets/{ruleset['id']}/definitions?ontology_id=7", headers=headers,
                json={"kind": "content", "schema_version": "1.0", "payload": {"description": "Notes"}})
    from app.db.session import session_factory
    from app.models.game_engine import GameRuleset
    with session_factory()() as db:
        revision = db.get(GameRuleset, ruleset["id"]).definition_revision
    rejected = client.post(f"/api/v1/game-engine/rulesets/{ruleset['id']}/reject?expected_revision={revision}",
                          headers=headers, json={"reason": "Source provenance needs review."})
    assert rejected.status_code == 200 and rejected.json()["status"] == "draft"
    history = client.get(f"/api/v1/game-engine/rulesets/{ruleset['id']}/reviews", headers=headers)
    assert history.status_code == 200
    assert history.json()[0]["decision"] == "rejected"
    assert history.json()[0]["reason"] == "Source provenance needs review."
