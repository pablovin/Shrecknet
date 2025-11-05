# Admin Clear Endpoints

This document describes the admin-only endpoints for clearing book embeddings and background jobs.

## Clear Library Item Embeddings

### Delete Embeddings for a Specific Library Item

```http
DELETE /libraries/{ontology_id}/items/{item_id}/embeddings
```

**Description**: Clears all embeddings for a specific library item by deleting all PdfChunk nodes in Neo4j associated with the item. The library item is also marked as not vectorized.

**Requires**: Admin role

**Path Parameters**:
- `ontology_id` (integer): The ID of the ontology
- `item_id` (integer): The ID of the library item

**Response** (200 OK):
```json
{
  "message": "Cleared embeddings for library item 5",
  "library_item_id": 5,
  "ontology_id": 1,
  "chunks_deleted": 150
}
```

**Example**:
```bash
curl -X DELETE \
  "http://localhost:8000/libraries/1/items/5/embeddings" \
  -H "Authorization: Bearer <admin-token>"
```

---

### Clear All Library Item Embeddings

```http
DELETE /libraries/admin/clear-all-embeddings?ontology_id={ontology_id}
```

**Description**: Clears all library item embeddings, optionally filtered by ontology. This deletes all PdfChunk nodes in Neo4j for all library items (or just those for a specific ontology) and marks all affected items as not vectorized.

**Requires**: Admin role

**Query Parameters**:
- `ontology_id` (integer, optional): Filter embeddings to clear by ontology ID

**Response** (200 OK):
```json
{
  "message": "Cleared embeddings for 10 library items",
  "items_affected": 10,
  "ontology_id": 1,
  "chunks_deleted": 1500
}
```

**Examples**:

Clear all embeddings for a specific ontology:
```bash
curl -X DELETE \
  "http://localhost:8000/libraries/admin/clear-all-embeddings?ontology_id=1" \
  -H "Authorization: Bearer <admin-token>"
```

Clear ALL embeddings across all ontologies:
```bash
curl -X DELETE \
  "http://localhost:8000/libraries/admin/clear-all-embeddings" \
  -H "Authorization: Bearer <admin-token>"
```

---

## Clear Background Jobs

### Clear All Background Jobs

```http
DELETE /jobs/admin/clear-all
```

**Description**: Clears all background jobs, optionally filtered by type, status, or ontology. By default, only jobs with status 'done' or 'failed' are deleted for safety.

**Requires**: Admin role

**Query Parameters**:
- `job_type` (string, optional): Filter by job type (e.g., `pdf_book_embedding`, `neo4j_embedding`, `backup`)
- `status` (string, optional): Filter by status (`queued`, `running`, `done`, `failed`)
- `ontology_id` (integer, optional): Filter by ontology ID

**Response** (200 OK):
```json
{
  "message": "Cleared 25 background jobs",
  "deleted_count": 25,
  "job_type": "pdf_book_embedding",
  "status": null,
  "ontology_id": 1
}
```

**Examples**:

Clear all completed/failed jobs:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all" \
  -H "Authorization: Bearer <admin-token>"
```

Clear only PDF embedding jobs:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all?job_type=pdf_book_embedding" \
  -H "Authorization: Bearer <admin-token>"
```

Clear jobs for a specific ontology:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all?ontology_id=1" \
  -H "Authorization: Bearer <admin-token>"
```

Clear only failed jobs:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all?status=failed" \
  -H "Authorization: Bearer <admin-token>"
```

---

## Use Cases

### Recovering from Stuck Embeddings

If a library item's embedding process gets stuck or corrupted:

1. Clear the embeddings for the specific item:
   ```bash
   DELETE /libraries/{ontology_id}/items/{item_id}/embeddings
   ```

2. Re-trigger the embedding process:
   ```bash
   POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding
   ```

### Cleaning Up Old Jobs

To clean up completed background jobs and reduce database clutter:

```bash
DELETE /jobs/admin/clear-all?status=done
```

### Re-embedding All Books in an Ontology

If you need to re-embed all books in an ontology (e.g., after upgrading the embedding model):

1. Clear all embeddings for the ontology:
   ```bash
   DELETE /libraries/admin/clear-all-embeddings?ontology_id=1
   ```

2. Re-trigger embedding for each library item

---

## Security

All endpoints require admin authentication. Attempting to access these endpoints without admin privileges will result in a 403 Forbidden response.

**Testing permissions**:
```bash
# This should return 403 for non-admin users
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all" \
  -H "Authorization: Bearer <player-token>"
```

---

## Safety Considerations

- **Clear embeddings operations are irreversible**. Make sure you want to delete the embeddings before running these commands.
- **Background job deletion** by default only deletes completed or failed jobs to prevent accidentally deleting running jobs.
- Consider backing up your database before performing bulk clear operations.
- Monitor your Neo4j database size after clearing large numbers of embeddings.

