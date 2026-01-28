# Architect API - Word-Based Chunking Examples

This document provides examples of using the updated Architect API with word-based chunking.

## Overview

The Architect job now chunks text based on **word count** instead of character count. This results in:
- More consistent chunk sizes
- Better semantic boundaries
- Reduced duplicate entity extractions
- Optimal chunk sizes for 4k-8k word stories

## Default Values

- **Default chunk size**: 1000 words (previously 1200 characters)
- **Default chunk overlap**: 100 words (previously 200 characters)
- **Recommended range**: 1000-2000 words per chunk

## API Endpoints

### 1. Start Architect Analysis

**Endpoint**: `POST /jobs/architect/{agent_id}/analyze`

**Request Body**:
```json
{
  "ontology_instance_id": "my-story-instance-123",
  "ontology_id": 42,
  "max_chunks": 10,
  "chunk_size": 1000
}
```

**Parameters**:
- `ontology_instance_id` (required): The ID of the story/instance to analyze
- `ontology_id` (optional): Ontology ID if instance ID is not unique
- `max_chunks` (optional): Maximum number of chunks to process (1-200)
- `chunk_size` (optional): Number of **words** per chunk (100-3000, default: 1000)

**Response**:
```json
{
  "id": "run-uuid-here",
  "agent_id": "agent-123",
  "background_job_id": 456,
  "ontology_id": 42,
  "ontology_instance_id": "my-story-instance-123",
  "status": "pending",
  "input_chunk_count": null,
  "settings": {
    "requested_by": "user-789",
    "max_chunks": 10,
    "chunk_size": 1000
  },
  "created_at": "2024-11-03T12:00:00Z",
  "updated_at": "2024-11-03T12:00:00Z",
  "proposals": []
}
```

## Usage Examples

### Example 1: Default Settings (1000-word chunks)

For a typical 4000-word story with default settings:

```bash
curl -X POST "http://localhost:8000/jobs/architect/agent-123/analyze" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "ontology_instance_id": "my-story-123"
  }'
```

Expected behavior:
- Story will be split into ~4 chunks of 1000 words each (with 100-word overlap)
- Results in approximately 5 chunks total due to overlap
- Each chunk analyzed independently for entity extraction

### Example 2: Larger Chunks (2000 words)

For an 8000-word story with larger chunks for broader context:

```bash
curl -X POST "http://localhost:8000/jobs/architect/agent-123/analyze" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "ontology_instance_id": "epic-story-456",
    "chunk_size": 2000,
    "max_chunks": 10
  }'
```

Expected behavior:
- Story split into ~4-5 chunks of 2000 words each
- Larger chunks provide more context to the LLM
- May reduce duplicate entity detections

### Example 3: Smaller Chunks (500 words)

For detailed analysis with fine-grained chunks:

```bash
curl -X POST "http://localhost:8000/jobs/architect/agent-123/analyze" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "ontology_instance_id": "short-story-789",
    "chunk_size": 500,
    "max_chunks": 20
  }'
```

### Example 4: Limited Analysis

For quick testing or preview, limit the number of chunks:

```bash
curl -X POST "http://localhost:8000/jobs/architect/agent-123/analyze" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "ontology_instance_id": "test-story-999",
    "max_chunks": 5
  }'
```

Expected behavior:
- Only first 5 chunks will be processed
- Faster processing for testing
- May not cover entire story

## Frontend TypeScript Example

```typescript
import { startArchitectAnalysis } from '@/app/lib/architectAPI';

// Example 1: Default settings
const run1 = await startArchitectAnalysis(
  agentId,
  {
    ontology_instance_id: 'my-story-123'
  },
  token
);

// Example 2: Custom chunk size
const run2 = await startArchitectAnalysis(
  agentId,
  {
    ontology_instance_id: 'epic-story-456',
    chunk_size: 2000,  // 2000 words per chunk
    max_chunks: 10
  },
  token
);

// Example 3: Small chunks for detailed analysis
const run3 = await startArchitectAnalysis(
  agentId,
  {
    ontology_instance_id: 'detailed-story-789',
    chunk_size: 500,  // 500 words per chunk
    max_chunks: 20
  },
  token
);
```

## Recommendations

### For Stories 4k-6k Words
```json
{
  "chunk_size": 1000,
  "max_chunks": 10
}
```
- Results in 4-6 chunks
- Good balance between context and granularity

### For Stories 6k-8k Words
```json
{
  "chunk_size": 1500,
  "max_chunks": 10
}
```
- Results in 5-7 chunks
- Maintains good context window

### For Stories 8k+ Words
```json
{
  "chunk_size": 2000,
  "max_chunks": 15
}
```
- Results in larger but more meaningful chunks
- Reduces duplicate entity extractions

## Benefits of Word-Based Chunking

1. **Reduced Duplicates**: Word boundaries are more natural than arbitrary character positions
2. **Better Context**: Each chunk contains complete words and likely complete sentences
3. **Consistent Sizing**: All chunks have similar word counts, leading to more predictable processing
4. **Semantic Integrity**: Less likely to split entities or concepts mid-word

## Migration Notes

If you were previously using character-based chunk sizes:
- **1200 characters** ≈ **200-250 words** (depending on average word length)
- **2400 characters** ≈ **400-500 words**
- **4096 characters** ≈ **680-850 words**

The new default of **1000 words** is roughly equivalent to **6000 characters**, providing much more context per chunk and reducing the total number of chunks for typical stories.
