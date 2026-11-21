import pytest
from sqlalchemy import create_engine, text

from app.api.deps import get_current_user
from app.db.world_identity_migration import migrate_world_identity
from app.main import app
from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_world_create_and_legacy_ontology_metadata(client):
    app.dependency_overrides[get_current_user] = lambda: User(id=1, role=UserRole.PLAYER)
    assert (await client.post("/worlds", json={"name": "Uther"})).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: User(id=1, role=UserRole.ADMIN)

    created = await client.post("/worlds", json={
        "name": "Britain", "rpg_system": "Pendragon", "rpg_edition": "6e",
    })
    assert created.status_code == 201, created.text
    world = created.json()
    assert world["rpg_edition"] == "6e"
    assert world["ontology_ids"] == [world["active_ontology_id"]]
    assert (await client.get(f"/contracts/worlds/{world['id']}")).json() == world
    ontology_path = f"/ontologies/{world['active_ontology_id']}"
    ontology = (await client.get(ontology_path)).json()
    assert ontology["world_id"] == world["id"]
    assert ontology["rpg_system"] == "Pendragon"
    assert (await client.delete(ontology_path)).status_code == 409

    updated = await client.patch(f"/worlds/{world['id']}", json={"description": "The Kingdom of Salt"})
    assert updated.status_code == 200, updated.text
    assert (await client.get(ontology_path)).json()["description"] == "The Kingdom of Salt"


def test_legacy_world_migration_preserves_ids_and_leaves_ambiguous_scopes():
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE worlds (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"))
        db.execute(text("""
            CREATE TABLE ontologies (
                id INTEGER PRIMARY KEY, world_id VARCHAR, name VARCHAR NOT NULL,
                description TEXT, image_url VARCHAR, rpg_system VARCHAR
            )
        """))
        db.execute(text("INSERT INTO worlds VALUES ('existing', 'Existing'), ('ambiguous', 'Ambiguous')"))
        db.execute(text("""
            INSERT INTO ontologies VALUES
            (1, NULL, 'Pendragon', 'Britain', '/image', 'Pendragon'),
            (2, 'existing', 'Existing ontology', 'Existing description', NULL, NULL),
            (3, 'ambiguous', 'First legacy scope', 'First', NULL, NULL),
            (4, 'ambiguous', 'Second legacy scope', 'Second', NULL, NULL)
        """))
        migrate_world_identity(db)
        migrate_world_identity(db)
        mappings = db.execute(text("SELECT id, world_id FROM ontologies ORDER BY id")).all()
        assert mappings == [(1, "world-onto-1"), (2, "existing"), (3, "ambiguous"), (4, "ambiguous")]
        metadata = db.execute(text(
            "SELECT id, name, description, image_url FROM worlds ORDER BY id"
        )).all()
        assert metadata == [
            ("ambiguous", "Ambiguous", None, None),
            ("existing", "Existing", "Existing description", None),
            ("world-onto-1", "Pendragon", "Britain", "/image"),
        ]
