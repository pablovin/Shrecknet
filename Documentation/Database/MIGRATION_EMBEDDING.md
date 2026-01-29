# Neo4j Embedding Migration

## Problem

When the embedding feature was added to Shrecknet, new EntityInstance nodes were created with `is_embedded` and `last_embedded_date` properties. However, existing nodes created before this feature was implemented don't have these properties.

This causes two issues:
1. The `/ontologies/{ontology_id}/embedding-stats` endpoint doesn't properly count legacy nodes
2. Legacy nodes won't be picked up by the embedding system

## Solution

A one-time migration that runs automatically on application startup to add these properties to all existing EntityInstance nodes that don't have them.

### What the migration does

1. Checks for EntityInstance nodes where `is_embedded IS NULL`
2. Sets `is_embedded = false` for these nodes
3. Sets `last_embedded_date = null` for these nodes

### When it runs

- **Automatically**: On every application startup via the `lifespan` context manager in `app/main.py`
- **Manually**: Admins can trigger it via `POST /ontologies/migrate-embedding-properties`

### Files changed

1. **app/db/migrations.py**
   - Added `migrate_neo4j_embedding_properties()` function

2. **app/main.py**
   - Updated `lifespan()` to call the migration on startup

3. **app/api/routers/ontologies.py**
   - Added `POST /ontologies/migrate-embedding-properties` endpoint for manual migration

4. **tests/test_neo4j_migrations.py**
   - Added comprehensive unit tests for the migration function

### Safety features

- **Idempotent**: Running the migration multiple times is safe - it only updates nodes that need it
- **Non-destructive**: Only adds properties, never removes or changes existing data
- **Logged**: All migration actions are logged for debugging
- **Tested**: Full test coverage to ensure correctness

### Example

Before migration:
```cypher
{
  entity_instance_id: "abc-123",
  ontology_id: 1,
  name: "Prince Constaninovitch",
  text: "<p>The russian Prince!</p>"
  // Missing: is_embedded, last_embedded_date
}
```

After migration:
```cypher
{
  entity_instance_id: "abc-123",
  ontology_id: 1,
  name: "Prince Constaninovitch",
  text: "<p>The russian Prince!</p>",
  is_embedded: false,
  last_embedded_date: null
}
```

Now this node will:
- Appear in embedding stats as an "unembedded" node
- Be picked up by the embedding job when triggered
- Work the same way as newly created nodes

### Testing

Run the tests:
```bash
cd backend
pytest tests/test_neo4j_migrations.py -v
```

### Manual migration

If needed, admins can manually trigger the migration:

```bash
curl -X POST http://localhost:8000/api/ontologies/migrate-embedding-properties \
  -H "Authorization: Bearer <admin-token>"
```

Response:
```json
{
  "nodes_migrated": 42,
  "status": "success",
  "message": "Successfully migrated 42 nodes with embedding properties"
}
```
