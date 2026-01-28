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

## Clean Agent Embedding Jobs

### Delete a Single Agent Embedding Job

```http
DELETE /jobs/agents/{agent_id}/embedding-jobs/{job_id}
```

**Description**: Deletes a specific embedding job (NEO4J_EMBEDDING or PDF_BOOK_EMBEDDING) for an agent. This is useful for cleaning up individual stuck jobs that are in QUEUED or RUNNING state.

**Requires**: Authentication (any authenticated user can delete jobs)

**Path Parameters**:
- `agent_id` (string): The ID of the agent
- `job_id` (integer): The ID of the embedding job to delete

**Response** (200 OK):
```json
{
  "message": "Deleted embedding job 123 for agent agent-abc-123",
  "job_id": 123,
  "agent_id": "agent-abc-123",
  "job_type": "neo4j_embedding",
  "status": "running"
}
```

**Error Responses**:
- 401 Unauthorized: Missing or invalid authentication
- 404 Not Found: Embedding job not found for the specified agent

**Examples**:

Delete a stuck embedding job:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs/456" \
  -H "Authorization: Bearer <token>"
```

---

### Delete Multiple Agent Embedding Jobs (Bulk)

```http
DELETE /jobs/agents/{agent_id}/embedding-jobs
```

**Description**: Deletes multiple embedding jobs (NEO4J_EMBEDDING or PDF_BOOK_EMBEDDING) for a specific agent. This is useful for cleaning up stuck jobs in bulk or clearing all embedding jobs for an agent.

**Requires**: Authentication (any authenticated user can delete jobs)

**Path Parameters**:
- `agent_id` (string): The ID of the agent

**Query Parameters**:
- `status` (string, optional): Filter by specific status (`queued`, `running`, `done`, `failed`)
- `ontology_id` (integer, optional): Filter by ontology ID
- `include_stuck_only` (boolean, optional): Only delete stuck jobs (QUEUED or RUNNING). Default: false

**Response** (200 OK):
```json
{
  "message": "Deleted 5 embedding job(s) for agent agent-abc-123",
  "deleted_count": 5,
  "agent_id": "agent-abc-123",
  "status": null,
  "ontology_id": 1,
  "include_stuck_only": true
}
```

**Error Responses**:
- 401 Unauthorized: Missing or invalid authentication

**Examples**:

Delete all stuck embedding jobs for an agent:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs?include_stuck_only=true" \
  -H "Authorization: Bearer <token>"
```

Delete all embedding jobs for an agent in a specific ontology:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs?ontology_id=1" \
  -H "Authorization: Bearer <token>"
```

Delete only failed embedding jobs for an agent:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs?status=failed" \
  -H "Authorization: Bearer <token>"
```

Delete all embedding jobs for an agent (including stuck ones):
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs" \
  -H "Authorization: Bearer <token>"
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

### Recovering from Stuck Agent Embedding Jobs

If an agent has stuck embedding jobs (in QUEUED or RUNNING state that aren't progressing):

1. Delete all stuck embedding jobs for the agent:
   ```bash
   DELETE /jobs/agents/{agent_id}/embedding-jobs?include_stuck_only=true
   ```

2. Re-trigger embedding for the affected ontologies

**For a specific stuck job**:
```bash
# Get the job_id from the frontend or API, then delete it
DELETE /jobs/agents/{agent_id}/embedding-jobs/{job_id}
```

### Cleaning Up Old Jobs

To clean up completed background jobs and reduce database clutter:

```bash
DELETE /jobs/admin/clear-all?status=done
```

**For a specific agent**:
```bash
# Clean up all completed embedding jobs for an agent
DELETE /jobs/agents/{agent_id}/embedding-jobs?status=done
```

### Re-embedding All Books in an Ontology

If you need to re-embed all books in an ontology (e.g., after upgrading the embedding model):

1. Clear all embeddings for the ontology:
   ```bash
   DELETE /libraries/admin/clear-all-embeddings?ontology_id=1
   ```

2. Clear any stuck or old embedding jobs for the agent:
   ```bash
   DELETE /jobs/agents/{agent_id}/embedding-jobs?ontology_id=1
   ```

3. Re-trigger embedding for each library item

---

## Security

**Admin endpoints** (prefixed with `/admin/`) require admin authentication. Attempting to access these endpoints without admin privileges will result in a 403 Forbidden response.

**Agent embedding endpoints** (`/jobs/agents/{agent_id}/embedding-jobs`) require authentication but do not require admin role. Any authenticated user can clean up embedding jobs for agents.

**Testing permissions**:
```bash
# This should return 403 for non-admin users
curl -X DELETE \
  "http://localhost:8000/jobs/admin/clear-all" \
  -H "Authorization: Bearer <player-token>"

# This should succeed for any authenticated user
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-123/embedding-jobs?include_stuck_only=true" \
  -H "Authorization: Bearer <player-token>"
```

---

## Safety Considerations

- **Clear embeddings operations are irreversible**. Make sure you want to delete the embeddings before running these commands.
- **Background job deletion** by default only deletes completed or failed jobs to prevent accidentally deleting running jobs.
- Consider backing up your database before performing bulk clear operations.
- Monitor your Neo4j database size after clearing large numbers of embeddings.

