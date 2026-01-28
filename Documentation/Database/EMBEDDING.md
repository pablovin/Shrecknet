# Neo4j Embedding System

This document describes the Neo4j embedding system, which tracks and manages embeddings for ontology nodes.

## Overview

The Neo4j embedding system provides:

- **Embedding Status Tracking**: Each `EntityInstance` node tracks whether it's embedded and when it was last embedded
- **Ontology-Level Embedding**: Trigger embedding jobs for entire ontologies
- **Embedding Statistics**: View counts of embedded/unembedded nodes per ontology
- **Job Monitoring**: Track embedding job progress with duration and status
- **Smart Re-embedding**: Only embeds nodes that are new or have been updated since last embedding

## Architecture

### Node Properties

Each `EntityInstance` node in Neo4j includes:

- `is_embedded` (boolean): Whether the node has been embedded
- `last_embedded_date` (datetime): When the node was last embedded
- `last_updated_date` (datetime): When the node was last modified
- `text_embedding` (list[float]): The actual embedding vector
- `text_embedding_model` (string): Model used for embedding
- `text_embedding_dim` (int): Dimension of the embedding vector
- `context_text` (string): The context text used to generate the embedding

### Background Jobs

Embedding jobs are tracked in the `background_jobs` table with:

- `ontology_id`: Links job to a specific ontology
- `duration_seconds`: Total time taken to complete the job
- `started_at`: Job start timestamp
- `completed_at`: Job completion timestamp
- `progress`: Current progress (0.0 to 1.0)
- `status`: Current status (queued, running, done, failed)
- `details`: JSON string with job-specific information

## API Endpoints

### Get Embedding Statistics

Get embedding statistics for a specific ontology.

```http
GET /ontologies/{ontology_id}/embedding-stats
Authorization: Bearer <token>
```

**Response** (200 OK):
```json
{
  "ontology_id": 1,
  "total_nodes": 150,
  "embedded_nodes": 120,
  "unembedded_nodes": 20,
  "outdated_nodes": 10
}
```

**Fields**:
- `total_nodes`: Total number of nodes in the ontology
- `embedded_nodes`: Nodes that have been successfully embedded
- `unembedded_nodes`: Nodes that have never been embedded
- `outdated_nodes`: Nodes that were embedded but have been updated since

### Trigger Embedding Job

Start a background job to embed all unembedded or outdated nodes in an ontology.

```http
POST /ontologies/{ontology_id}/trigger-embedding
Authorization: Bearer <admin-or-world-builder-token>
Content-Type: application/json

{}
```

**Response** (202 Accepted):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "ontology_id": 1,
  "message": "Embedding job triggered for ontology 1"
}
```

**Permissions**: Requires `ADMIN` or `WORLD_BUILDER` role.

### Get Embedding Jobs

Get recent embedding jobs for a specific ontology.

```http
GET /ontologies/{ontology_id}/embedding-jobs?limit=10
Authorization: Bearer <token>
```

**Query Parameters**:
- `limit`: Maximum number of jobs to return (default: 10, max: 10)

**Response** (200 OK):
```json
[
  {
    "kind": "neo4j_embedding",
    "job_id": "1",
    "start_time": "2025-10-29T10:00:00Z",
    "status": "done",
    "author_type": "user",
    "author_id": "42",
    "description": "Embedding nodes for ontology 1",
    "details": "{\"nodes_processed\": 100, \"nodes_failed\": 0}",
    "progress": 1.0,
    "error_message": null,
    "completed_at": "2025-10-29T10:02:30Z",
    "duration_seconds": 150.5,
    "ontology_id": 1,
    "updated_at": "2025-10-29T10:02:30Z"
  }
]
```

## Embedding Process

### How It Works

1. **Identify Nodes**: Query finds all nodes that need embedding:
   - Never been embedded (`is_embedded` is NULL or false)
   - Updated since last embedding (`last_updated_date > last_embedded_date`)

2. **Build Context**: For each node, build context text from:
   - Node name and labels
   - Ontology path
   - Node properties
   - Relationships (up to 6)
   - Summary text

3. **Generate Embeddings**: Use sentence-transformers model to create embeddings in batches

4. **Update Nodes**: Mark nodes with:
   - `is_embedded = true`
   - `last_embedded_date = current_datetime()`
   - `text_embedding = [vector]`
   - `context_text = [context used for embedding]`

5. **Track Progress**: Update background job with progress percentage and statistics

### Batch Processing

Nodes are processed in batches (default: 50) to:
- Optimize memory usage
- Provide granular progress updates
- Allow for graceful failure handling

### Error Handling

- Individual node failures don't stop the entire job
- Failed nodes are counted but the job continues
- Error details are stored in job details
- Job status is marked as failed only if the entire job fails

## Monitoring Jobs

### Job Status

- `queued`: Job is waiting to start
- `running`: Job is currently processing
- `done`: Job completed successfully
- `failed`: Job failed with errors

### Progress Tracking

The embedding job updates progress at these milestones:
- 10%: Fetching nodes to embed
- 20%: Found N nodes to embed
- 30%: Ensuring vector index
- 40%: Starting embedding process
- 90%: Marking nodes as embedded
- 100%: Job complete

### Duration Metrics

Each job tracks:
- `started_at`: When the job was created
- `completed_at`: When the job finished (success or failure)
- `duration_seconds`: Total execution time in seconds

### Job Details

The `details` field contains JSON with job-specific information:

**Initial Details**:
```json
{
  "ontology_id": 1
}
```

**Completion Details**:
```json
{
  "nodes_processed": 95,
  "nodes_failed": 5,
  "total_found": 100,
  "status": "success"
}
```

## Best Practices

### When to Trigger Embedding

1. **After Bulk Import**: After importing new ontology instances
2. **After Major Updates**: After updating many nodes
3. **Scheduled**: Set up periodic re-embedding for large ontologies
4. **On Demand**: Before performing semantic search or retrieval tasks

### Monitoring

1. **Check Statistics**: Regularly review embedding statistics to identify nodes that need embedding
2. **Review Job History**: Look at recent jobs to understand processing patterns
3. **Monitor Duration**: Track job duration to identify performance issues
4. **Check Error Rates**: Review failed node counts to identify data quality issues

### Performance Considerations

1. **Batch Size**: Default is 50 nodes per batch; adjust based on memory and speed needs
2. **Node Count**: Large ontologies (1000+ nodes) may take several minutes to embed
3. **Model Loading**: First embedding job loads the model into memory (one-time cost)
4. **Vector Index**: Index is created once per ontology automatically

## Integration with GraphRAG

Embeddings are used by the GraphRAG system for:

- **Semantic Search**: Finding relevant nodes based on query similarity
- **Context Retrieval**: Retrieving related information for LLM queries
- **Elder Agent**: Answering questions using embedded knowledge
- **Relationship Discovery**: Finding semantically similar nodes

## Troubleshooting

### No Nodes Being Embedded

**Symptoms**: Job completes but nodes_processed is 0

**Causes**:
- All nodes already embedded and up-to-date
- No nodes exist in the ontology
- Ontology ID mismatch

**Solution**: Check embedding statistics first

### Embedding Job Fails

**Symptoms**: Job status is "failed"

**Causes**:
- Neo4j connection issues
- Model loading failures
- Invalid node data
- Memory issues

**Solution**: Check job error_message and logs

### Slow Embedding Performance

**Symptoms**: Jobs take very long to complete

**Causes**:
- Large batch sizes
- Many relationships per node
- Model not cached
- Slow Neo4j queries

**Solutions**:
- Reduce batch size
- Limit relationship context
- Warm up model cache
- Check Neo4j indexes

### Outdated Nodes Not Re-embedding

**Symptoms**: Nodes show as outdated but don't get re-embedded

**Causes**:
- `last_updated_date` not being set on updates
- Timezone comparison issues

**Solution**: Ensure ontology instance updates set `last_updated_date`

## Example Workflows

### Complete Ontology Embedding

```bash
# 1. Check current statistics
curl -X GET "http://localhost:8000/ontologies/1/embedding-stats" \
  -H "Authorization: Bearer $TOKEN"

# 2. Trigger embedding if needed
curl -X POST "http://localhost:8000/ontologies/1/trigger-embedding" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# 3. Monitor job progress
curl -X GET "http://localhost:8000/ontologies/1/embedding-jobs?limit=1" \
  -H "Authorization: Bearer $TOKEN"
```

### Python Integration

```python
import httpx
import asyncio

async def embed_ontology_and_wait(ontology_id: int, token: str):
    """Trigger embedding and wait for completion."""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        headers = {"Authorization": f"Bearer {token}"}
        
        # Trigger embedding
        response = await client.post(
            f"/ontologies/{ontology_id}/trigger-embedding",
            headers=headers,
            json={}
        )
        result = response.json()
        print(f"Job triggered: {result['job_id']}")
        
        # Poll for completion
        while True:
            await asyncio.sleep(5)
            
            # Get latest job
            response = await client.get(
                f"/ontologies/{ontology_id}/embedding-jobs?limit=1",
                headers=headers
            )
            jobs = response.json()
            
            if not jobs:
                continue
                
            job = jobs[0]
            print(f"Status: {job['status']}, Progress: {job['progress']}")
            
            if job['status'] in ['done', 'failed']:
                print(f"Duration: {job['duration_seconds']}s")
                if job['details']:
                    print(f"Details: {job['details']}")
                break

# Usage
await embed_ontology_and_wait(ontology_id=1, token="your-token")
```

---

## PDF Book Embedding

The PDF book embedding system extends the Neo4j embedding infrastructure to support PDF rulebooks and game materials for the Librarian job.

### Overview

PDF books are embedded as page-level chunks in Neo4j, enabling:

- **Semantic Search**: Find relevant sections based on meaning, not just keywords
- **Page Citations**: Precise page number references in answers
- **Multi-Book Search**: Search across all books in an ontology
- **Background Processing**: Non-blocking embedding via Celery tasks

### Architecture

#### Node Schema

Each page becomes a `PdfChunk` node with:

- `library_item_id` (int): Links to library item
- `ontology_id` (int): Links to ontology for scoping
- `chunk_index` (int): Unique index for this chunk
- `page_number` (int): Page number in the PDF (1-indexed)
- `text` (string): Extracted text content
- `text_embedding` (float[]): Vector embedding (384-dim)
- `text_embedding_model` (string): Model identifier
- `text_embedding_dim` (int): Embedding dimensionality
- `last_embedded_date` (datetime): When embedded

#### Vector Index

- **Index Name**: `pdf_chunk_text_vec_idx`
- **Node Label**: `PdfChunk`
- **Property**: `text_embedding`
- **Similarity**: Cosine
- **Dimensions**: 384 (paraphrase-multilingual-MiniLM-L12-v2)

### API Endpoints

#### Trigger PDF Embedding

```http
POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding
Authorization: Bearer <admin-or-world-builder-token>
```

Triggers a background Celery task to embed the PDF.

**Response** (202 Accepted):
```json
{
  "message": "Embedding job triggered for library item 5",
  "library_item_id": 5,
  "ontology_id": 1,
  "celery_task_id": "task-uuid-123"
}
```

#### Check Embedding Status

```http
GET /libraries/{ontology_id}/items/{item_id}/embedding-status
Authorization: Bearer <token>
```

**Response**:
```json
{
  "library_item_id": 5,
  "ontology_id": 1,
  "vectorized": true,
  "last_vectorized_at": "2025-10-29T12:00:00Z",
  "total_chunks": 320,
  "is_embedded": true
}
```

#### List Embedding Jobs

```http
GET /libraries/embedding-jobs?ontology_id=1&limit=10
Authorization: Bearer <token>
```

Returns recent embedding jobs with status and progress.

### Embedding Process

1. **Read PDF**: Uses PyPDF2 to extract text from each page
2. **Chunk Pages**: Each page becomes a separate chunk for precise citations
3. **Generate Embeddings**: Uses sentence-transformers in batches
4. **Store in Neo4j**: Creates `PdfChunk` nodes with embeddings
5. **Update Status**: Marks library item as vectorized

### Background Job

**Task**: `library.embed_pdf_book`

**Job Type**: `pdf_book_embedding`

**Progress Stages**:
- 10%: Fetching library item
- 20%: Reading PDF file
- 30%: Ensuring vector index
- 40%: Embedding pages (updates throughout)
- 90%: Updating library item status
- 100%: Complete

**Batch Size**: 20 pages per batch (configurable)

### Search and Retrieval

The Librarian job uses `PdfEmbeddingService.search_chunks()` to:

1. Generate query embedding
2. Perform vector similarity search in Neo4j
3. Filter by ontology_id and optional library_item_ids
4. Return top-k chunks with scores and page numbers

### Performance

**Embedding Speed**:
- ~100-200 pages per minute (depends on content)
- First run loads model into memory (~120MB)
- Subsequent runs reuse cached model

**Search Speed**:
- ~100-500ms for semantic search
- O(log n) with vector index
- Scales well to thousands of chunks

### Best Practices

#### When to Embed

1. **After Upload**: Trigger immediately after PDF upload
2. **After Update**: Re-embed if PDF is replaced
3. **Batch Operations**: Embed multiple books during off-peak hours

#### Monitoring

1. **Check Status**: Use embedding-status endpoint before queries
2. **Review Jobs**: Monitor embedding-jobs for failures
3. **Verify Chunks**: Ensure chunk count matches expected pages

#### Optimization

1. **Batch Size**: Adjust based on memory (default 20 is safe)
2. **Filter by Book**: Use library_item_ids when searching specific books
3. **Score Threshold**: Use 0.3-0.5 for PDF content (lower than entity search)

### Troubleshooting

#### PDF Extraction Fails

**Symptoms**: Job completes but 0 chunks created

**Causes**:
- Scanned PDF without OCR
- Encrypted/protected PDF
- Corrupted PDF file

**Solution**: Verify PDF is text-based and readable

#### Slow Embedding

**Symptoms**: Job takes very long

**Causes**:
- Large PDF (1000+ pages)
- Complex page layouts
- First-time model loading

**Solutions**:
- Process large PDFs during off-peak
- Reduce batch size if memory-constrained
- Allow extra time for first embedding

#### Missing Chunks in Search

**Symptoms**: Known content not returned

**Causes**:
- Score threshold too high
- Query phrasing mismatch
- Chunks not embedded yet

**Solutions**:
- Lower score_threshold to 0.3
- Rephrase query
- Check embedding-status

### Integration with Librarian

The Librarian job automatically:

1. Searches across all embedded PDFs in agent's ontologies
2. Retrieves top-k relevant chunks
3. Generates answers with page citations
4. Filters by library_item_ids if specified

See AGENTIC_JOBS.md for full Librarian documentation.

## Cleaning Up Embedding Jobs

### Managing Stuck Jobs

Sometimes embedding jobs can get stuck in QUEUED or RUNNING state without progressing. New endpoints are available to clean up these stuck jobs:

#### Delete a Single Stuck Job

```bash
DELETE /jobs/agents/{agent_id}/embedding-jobs/{job_id}
```

This deletes a specific embedding job for an agent. Useful when you know the exact job ID that's stuck.

#### Delete Multiple Stuck Jobs

```bash
DELETE /jobs/agents/{agent_id}/embedding-jobs?include_stuck_only=true
```

This deletes all stuck embedding jobs (QUEUED or RUNNING) for a specific agent. You can also filter by:
- `status`: Delete jobs with a specific status
- `ontology_id`: Delete jobs for a specific ontology

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

Delete only failed embedding jobs:
```bash
curl -X DELETE \
  "http://localhost:8000/jobs/agents/agent-abc-123/embedding-jobs?status=failed" \
  -H "Authorization: Bearer <token>"
```

See ADMIN_CLEAR_ENDPOINTS.md for complete documentation on all cleanup endpoints.

## Future Enhancements

Potential improvements to the embedding system:

- **Incremental Updates**: Re-embed only changed nodes automatically on update
- **Multiple Models**: Support for different embedding models per ontology
- **Embedding Comparison**: Compare embedding quality between models
- **Automatic Scheduling**: Cron-like scheduling for periodic re-embedding
- **Parallel Processing**: Multi-worker support for faster embedding
- **Vector Search Optimization**: Advanced indexing strategies for large ontologies
- **Embedding Quality Metrics**: Track and report embedding quality scores
