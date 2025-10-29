# Agentic Jobs

AI-powered jobs that provide intelligent, context-aware responses using LLM orchestration and graph retrieval.

## Overview

Agentic jobs are admin-managed AI agents that execute multi-step workflows to answer user queries. Each agent:
- **Persists in SQLite** with configuration (name, avatar, description, writing style, job type)
- **Links to ontologies** to scope knowledge retrieval
- **Executes job pipelines** using LLM orchestration and Neo4j graph retrieval
- **Applies writing styles** to customize response tone and personality

## Available Jobs

### Elder Job

The Elder job provides intelligent question-answering with context-aware retrieval from your knowledge graphs.

#### Pipeline Steps

1. **Decompose** - Breaks user query into 1-5 focused sub-queries based on linked ontologies
2. **Retrieve** - Performs parallel semantic search in Neo4j for each sub-query
3. **Sub-answer** - Generates answers for each sub-query using only retrieved context
4. **Synthesize** - Combines sub-answers into a coherent final answer
5. **Validate** - Checks answer completeness and refines if needed
6. **Style** - Applies agent's writing style while preserving factual accuracy

#### Response Modes

- **`nl`** - Returns only the natural language answer
- **`context`** - Returns only retrieved context and important nodes
- **`both`** - Returns both answer and context (default)

#### Configuration

**LLM Models** (configurable via environment variables):
- Decompose: `gpt-4o-mini` (`BACKEND_2_MODEL_DECOMPOSE`)
- Sub-answer: `gpt-4o-mini` (`BACKEND_2_MODEL_SUBANSWER`)
- Synthesis: `gpt-4o` (`BACKEND_2_MODEL_SYNTHESIS`)
- Validation: `gpt-4o-mini` (`BACKEND_2_MODEL_VALIDATION`)
- Style: `gpt-4o-mini` (`BACKEND_2_MODEL_STYLE`)

**Pipeline Settings**:
- `BACKEND_2_DEFAULT_TOP_K` - Results per sub-query (default: 8)
- `BACKEND_2_ENABLE_TRACING` - Include execution trace (default: true)
- `BACKEND_2_RATE_LIMIT_RPM` - Optional rate limiting

## Agent Management

### Prerequisites

- Admin role required for all agent management operations
- OpenAI API key configured (`BACKEND_2_OPENAI_API_KEY`)
- Neo4j connection configured for retrieval

### Create an Agent

```http
POST /agents/
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "name": "Elder Sage",
  "avatar_url": "https://example.com/avatar.png",
  "description": "A wise elder who provides thoughtful answers about the world",
  "writing_style": "Concise and wise, speaking in measured tones with occasional metaphors",
  "job": "elder",
  "ontology_ids": [1, 2, 5],
  "active": true
}
```

**Response** (201 Created):
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Elder Sage",
  "avatar_url": "https://example.com/avatar.png",
  "description": "A wise elder who provides thoughtful answers about the world",
  "writing_style": "Concise and wise, speaking in measured tones with occasional metaphors",
  "job": "elder",
  "active": true,
  "ontology_ids": [1, 2, 5],
  "created_at": "2025-10-29T10:00:00Z",
  "updated_at": "2025-10-29T10:00:00Z"
}
```

### List Agents

```http
GET /agents/?job=elder&active=true&limit=50&offset=0
Authorization: Bearer <admin-token>
```

**Query Parameters**:
- `job` - Filter by job type (optional)
- `active` - Filter by active status (optional)
- `limit` - Maximum results (default: 100, max: 1000)
- `offset` - Pagination offset (default: 0)

### Update an Agent

```http
PATCH /agents/{agent_id}
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "name": "Elder Sage Updated",
  "active": false
}
```

### Manage Ontology Links

**Attach ontology**:
```http
POST /agents/{agent_id}/ontologies/{ontology_id}
Authorization: Bearer <admin-token>
```

**Detach ontology**:
```http
DELETE /agents/{agent_id}/ontologies/{ontology_id}
Authorization: Bearer <admin-token>
```

### Delete an Agent

```http
DELETE /agents/{agent_id}
Authorization: Bearer <admin-token>
```

### Get Available Job Types

```http
GET /agents/jobs
Authorization: Bearer <admin-token>
```

**Response**:
```json
["elder"]
```

## Querying an Agent

### Elder Query Endpoint

```http
POST /jobs/elder/{agent_id}/query
Authorization: Bearer <user-token>
Content-Type: application/json

{
  "query": "Who is the Prince of Chicago and what is their relationship to the Camarilla?",
  "mode": "both",
  "top_k": 8,
  "include_trace": false
}
```

**Request Parameters**:
- `query` (required) - The user's question (1-2000 characters)
- `mode` - Response mode: `"nl"`, `"context"`, or `"both"` (default: `"both"`)
- `top_k` - Number of retrieval results per sub-query (default: from config, max: 50)
- `include_trace` - Include execution trace for debugging (default: false)
- `chat_id` - Optional chat ID to use conversation history as context and save messages (default: null)

**Note**: When `chat_id` is provided:
- The last 10 messages from the chat are used as context during query decomposition
- Both the user's query and the assistant's answer are saved to the chat history
- Chat history helps maintain conversation context across multiple queries

**Response** (200 OK):
```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "mode": "both",
  "query": "Who is the Prince of Chicago and what is their relationship to the Camarilla?",
  "answer": "Based on the available information, Lodin serves as the Prince of Chicago. As a Ventrue elder, he maintains the city's adherence to Camarilla traditions and enforces the Masquerade. His position grants him significant authority within the Camarilla power structure, though he must balance this with the interests of other influential elders in the domain.",
  "subanswers": [
    {
      "subquery": "Who holds the position of Prince in Chicago?",
      "answer": "According to the records, Lodin is the Prince of Chicago. He is a Ventrue elder who has held this position for several decades.",
      "retrieval": [
        {
          "node_id": "entity-lodin-123",
          "node_label": "Character",
          "text": "Lodin: Ventrue Prince of Chicago. Known for his strict enforcement of the Masquerade and traditional Camarilla values.",
          "score": 0.94,
          "source": "ontology_1"
        }
      ]
    },
    {
      "subquery": "What is the relationship between the Prince of Chicago and the Camarilla?",
      "answer": "The Prince serves as the Camarilla's primary authority in the city, responsible for maintaining order and enforcing sect policies.",
      "retrieval": [
        {
          "node_id": "entity-camarilla-456",
          "node_label": "Organization",
          "text": "The Camarilla: Vampire sect that emphasizes the Masquerade. Princes serve as local authorities in each domain.",
          "score": 0.88,
          "source": "ontology_2"
        }
      ]
    }
  ],
  "important_nodes": [
    "entity-lodin-123",
    "entity-camarilla-456",
    "entity-chicago-789"
  ],
  "context": [
    {
      "node_id": "entity-lodin-123",
      "node_label": "Character",
      "text": "Lodin: Ventrue Prince of Chicago. Known for his strict enforcement of the Masquerade and traditional Camarilla values.",
      "score": 0.94,
      "source": "ontology_1"
    },
    {
      "node_id": "entity-camarilla-456",
      "node_label": "Organization",
      "text": "The Camarilla: Vampire sect that emphasizes the Masquerade. Princes serve as local authorities in each domain.",
      "score": 0.88,
      "source": "ontology_2"
    }
  ],
  "trace": null
}
```

### With Trace Enabled

When `include_trace: true`:

```json
{
  "...": "...",
  "trace": [
    {
      "step": "decompose",
      "data": {
        "subqueries": [
          "Who holds the position of Prince in Chicago?",
          "What is the relationship between the Prince of Chicago and the Camarilla?"
        ],
        "model": "gpt-4o-mini"
      }
    },
    {
      "step": "retrieve",
      "data": {
        "retrieval": [
          {
            "subquery": "Who holds the position of Prince in Chicago?",
            "num_chunks": 6
          },
          {
            "subquery": "What is the relationship between the Prince of Chicago and the Camarilla?",
            "num_chunks": 4
          }
        ]
      }
    },
    {
      "step": "subanswer",
      "data": {
        "subanswers": [
          {
            "subquery": "Who holds the position of Prince in Chicago?",
            "answer_preview": "According to the records, Lodin is the Prince of Chicago. He is a Ventrue elder who has held..."
          }
        ],
        "model": "gpt-4o-mini"
      }
    },
    {
      "step": "synthesize",
      "data": {
        "answer_preview": "Based on the available information, Lodin serves as the Prince of Chicago. As a Ventrue elder...",
        "model": "gpt-4o"
      }
    },
    {
      "step": "validate",
      "data": {
        "validation": "OK",
        "is_ok": true,
        "model": "gpt-4o-mini"
      }
    },
    {
      "step": "style",
      "data": {
        "styled_preview": "In the grand tradition of the Camarilla, Lodin stands as Chicago's Prince...",
        "model": "gpt-4o-mini"
      }
    }
  ]
}
```

## Error Responses

### Agent Not Found
```json
{
  "detail": "Agent not found"
}
```
Status: 404

### Agent Inactive
```json
{
  "detail": "Agent is not active"
}
```
Status: 400

### Invalid Job Type
```json
{
  "detail": "Agent job type 'other' is not 'elder'"
}
```
Status: 400

### OpenAI API Key Not Configured
```json
{
  "detail": "OpenAI API key not configured"
}
```
Status: 503

### Query Execution Failed
```json
{
  "detail": "Elder query execution failed: <error details>"
}
```
Status: 500

## Chat Management

The Elder job supports persistent chat sessions that maintain conversation history. Each user can create up to 10 separate chats per agent, allowing them to organize different topics or contexts.

### Chat Features

- **Persistent History**: Conversations are saved and can be referenced in future queries
- **Context Awareness**: Recent chat history (last 10 messages) is used during query decomposition
- **Organization**: Each chat can have a custom name and color for easy identification
- **Privacy**: Users can only access their own chats
- **Limit**: Maximum 10 chats per user per agent

### Create a Chat

```http
POST /jobs/elder/chats/
Authorization: Bearer <user-token>
Content-Type: application/json

{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "World Politics Discussion",
  "color": "#FF5733"
}
```

**Request Parameters**:
- `agent_id` (required) - The ID of the elder agent
- `name` (required) - Chat name (1-100 characters)
- `color` (optional) - Hex color code (e.g., `#FF5733`)

**Response** (201 Created):
```json
{
  "id": "chat-uuid-here",
  "user_id": 123,
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "World Politics Discussion",
  "color": "#FF5733",
  "created_at": "2025-10-29T10:00:00Z",
  "updated_at": "2025-10-29T10:00:00Z",
  "message_count": 0
}
```

### List User Chats

```http
GET /jobs/elder/chats/?agent_id={agent_id}&limit=100&offset=0
Authorization: Bearer <user-token>
```

**Query Parameters**:
- `agent_id` (optional) - Filter chats by agent
- `limit` (optional) - Maximum results (default: 100, max: 1000)
- `offset` (optional) - Pagination offset (default: 0)

**Response** (200 OK):
```json
{
  "chats": [
    {
      "id": "chat-uuid-1",
      "user_id": 123,
      "agent_id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "World Politics Discussion",
      "color": "#FF5733",
      "created_at": "2025-10-29T10:00:00Z",
      "updated_at": "2025-10-29T12:30:00Z",
      "message_count": 8
    },
    {
      "id": "chat-uuid-2",
      "user_id": 123,
      "agent_id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Character Backstories",
      "color": "#3498DB",
      "created_at": "2025-10-28T15:00:00Z",
      "updated_at": "2025-10-29T09:00:00Z",
      "message_count": 15
    }
  ],
  "total": 2,
  "limit": 100,
  "offset": 0
}
```

### Get Chat with History

```http
GET /jobs/elder/chats/{chat_id}?include_history=true
Authorization: Bearer <user-token>
```

**Query Parameters**:
- `include_history` (optional) - Include chat messages (default: false)

**Response** (200 OK):
```json
{
  "id": "chat-uuid-1",
  "user_id": 123,
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "World Politics Discussion",
  "color": "#FF5733",
  "created_at": "2025-10-29T10:00:00Z",
  "updated_at": "2025-10-29T12:30:00Z",
  "message_count": 4,
  "history": [
    {
      "id": 1,
      "chat_id": "chat-uuid-1",
      "role": "user",
      "content": "Who is the Prince of Chicago?",
      "created_at": "2025-10-29T10:05:00Z"
    },
    {
      "id": 2,
      "chat_id": "chat-uuid-1",
      "role": "assistant",
      "content": "Lodin is the Prince of Chicago. He is a Ventrue elder...",
      "created_at": "2025-10-29T10:05:15Z"
    },
    {
      "id": 3,
      "chat_id": "chat-uuid-1",
      "role": "user",
      "content": "Tell me more about his relationship with the Camarilla.",
      "created_at": "2025-10-29T12:30:00Z"
    },
    {
      "id": 4,
      "chat_id": "chat-uuid-1",
      "role": "assistant",
      "content": "As Prince, Lodin serves as the Camarilla's primary authority...",
      "created_at": "2025-10-29T12:30:10Z"
    }
  ]
}
```

**Note**: History is limited to the most recent 50 messages when `include_history=true`.

### Update Chat

```http
PATCH /jobs/elder/chats/{chat_id}
Authorization: Bearer <user-token>
Content-Type: application/json

{
  "name": "Updated Chat Name",
  "color": "#00FF00"
}
```

**Request Parameters** (all optional):
- `name` - New chat name (1-100 characters)
- `color` - New hex color code

**Response** (200 OK):
```json
{
  "id": "chat-uuid-1",
  "user_id": 123,
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Updated Chat Name",
  "color": "#00FF00",
  "created_at": "2025-10-29T10:00:00Z",
  "updated_at": "2025-10-29T13:00:00Z",
  "message_count": 4
}
```

### Delete Chat

```http
DELETE /jobs/elder/chats/{chat_id}
Authorization: Bearer <user-token>
```

**Response** (204 No Content)

Deletes the chat and all its message history permanently.

### Using Chats in Queries

To use a chat's history as context and save the conversation:

```http
POST /jobs/elder/{agent_id}/query
Authorization: Bearer <user-token>
Content-Type: application/json

{
  "query": "Tell me more about his relationship with the Primogen.",
  "chat_id": "chat-uuid-1",
  "mode": "nl"
}
```

The Elder will:
1. Load the last 10 messages from the chat history
2. Use this context during query decomposition for better understanding
3. Save both the user's query and the assistant's answer to the chat

This enables natural, context-aware follow-up questions.

### Chat Error Responses

#### Chat Not Found
```json
{
  "detail": "Chat not found"
}
```
Status: 404

#### Maximum Chats Exceeded
```json
{
  "detail": "Maximum of 10 chats per agent reached. Please delete a chat before creating a new one."
}
```
Status: 400

#### Invalid Color Format
```json
{
  "detail": "Input should be a valid string"
}
```
Status: 422

## Usage Examples

### Frontend Integration

```typescript
// Create an agent (admin only)
const createAgent = async (token: string) => {
  const response = await fetch('/agents/', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      name: 'Story Elder',
      description: 'An elder who helps narrate your story',
      writing_style: 'Dramatic and narrative, like a storyteller',
      job: 'elder',
      ontology_ids: [1, 2],
      active: true
    })
  });
  return await response.json();
};

// Query an agent
const queryAgent = async (agentId: string, query: string, token: string) => {
  const response = await fetch(`/jobs/elder/${agentId}/query`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      query: query,
      mode: 'both',
      top_k: 8,
      include_trace: false
    })
  });
  return await response.json();
};

// Usage
const agent = await createAgent(adminToken);
const result = await queryAgent(
  agent.id,
  'Tell me about the political structure of the city',
  userToken
);

console.log(result.answer);
// Display important_nodes as clickable references
// Show context for transparency
```

### Python Integration

```python
import httpx

async def query_elder(agent_id: str, query: str, token: str) -> dict:
    """Query an Elder agent."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://localhost:8000/jobs/elder/{agent_id}/query",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "query": query,
                "mode": "both",
                "top_k": 8,
                "include_trace": False
            }
        )
        response.raise_for_status()
        return response.json()

# Usage
result = await query_elder(
    agent_id="550e8400-e29b-41d4-a716-446655440000",
    query="What are the key factions in this world?",
    token="user_token_here"
)

print(f"Answer: {result['answer']}")
print(f"Based on {len(result['important_nodes'])} key entities")
```

## Best Practices

### Agent Configuration

1. **Name**: Use descriptive names that indicate the agent's purpose (e.g., "Lore Elder", "Story Guide")
2. **Writing Style**: Be specific about tone, formality, and personality traits
3. **Ontologies**: Link only relevant ontologies to keep retrieval focused and accurate
4. **Active Status**: Set `active: false` for agents under development or maintenance

### Query Optimization

1. **Ask Focused Questions**: Better results come from specific, well-formed questions
2. **Use Context Mode**: For transparency, use `mode: "both"` to show sources
3. **Adjust top_k**: Increase for complex queries, decrease for faster responses
4. **Enable Tracing**: Use during development to debug pipeline behavior

### Writing Styles

Examples of effective writing styles:

- **Concise**: "Brief and direct, avoiding unnecessary elaboration"
- **Academic**: "Formal and scholarly, citing sources and using precise terminology"
- **Narrative**: "Story-driven and descriptive, painting vivid scenes"
- **Conversational**: "Friendly and approachable, like chatting with a friend"
- **Mystical**: "Enigmatic and poetic, speaking in riddles and metaphors"

## Monitoring and Debugging

### Execution Trace

Enable tracing to see:
- Sub-queries generated from the original query
- Number of chunks retrieved per sub-query
- Validation results (OK or refinement needed)
- Model used for each step

### Logs

Check application logs for:
- Query execution timing
- LLM API errors
- Neo4j retrieval failures
- Session management issues

### Performance

Expected latency (varies by query complexity):
- Simple queries: 3-8 seconds
- Complex queries: 10-20 seconds
- With refinement: +5-10 seconds

Factors affecting performance:
- Number of sub-queries generated (1-5)
- Top-k setting (higher = more retrieval)
- LLM model speed (gpt-4o slower than gpt-4o-mini)
- Neo4j index performance

## Future Jobs

Additional job types can be added following the same pattern:
- `writer` - Generate creative content based on world lore
- `analyzer` - Analyze relationships and patterns in the knowledge graph
- `summarizer` - Create summaries of large knowledge bases
- `validator` - Check content consistency and accuracy

Each job type will have its own pipeline implementation in `app/jobs/<job_type>/`.

---

## Librarian Job

The Librarian job provides intelligent question-answering from embedded PDF rulebooks and game materials.

### Pipeline Steps

1. **Retrieve** - Performs semantic search across embedded PDF chunks based on user query
2. **Answer** - Generates comprehensive answer from retrieved book excerpts
3. **Style** - Applies agent's writing style while preserving factual accuracy and citations

### Response Modes

- **`nl`** - Returns only the natural language answer
- **`context`** - Returns only retrieved PDF chunks with page numbers
- **`both`** - Returns both answer and chunks (default)

### Configuration

**LLM Models** (configurable via environment variables):
- Answer: `gpt-4o` (`BACKEND_2_MODEL_SYNTHESIS`)
- Style: `gpt-4o-mini` (`BACKEND_2_MODEL_STYLE`)

**Pipeline Settings**:
- `BACKEND_2_DEFAULT_TOP_K` - Results per query (default: 8)
- `BACKEND_2_ENABLE_TRACING` - Include execution trace (default: true)

### PDF Embedding

Before using the Librarian, PDFs must be embedded:

1. **Upload PDF** to library via `/libraries/{ontology_id}/items`
2. **Trigger Embedding** via `/libraries/{ontology_id}/items/{item_id}/trigger-embedding`
3. **Monitor Progress** via `/libraries/embedding-jobs`

Embeddings are stored in Neo4j as `PdfChunk` nodes with:
- Page-level chunking for precise citations
- Vector embeddings using `paraphrase-multilingual-MiniLM-L12-v2`
- Metadata: library_item_id, ontology_id, page_number

### Create a Librarian Agent

```http
POST /agents/
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "name": "Tome Keeper",
  "avatar_url": "https://example.com/librarian.png",
  "description": "A knowledgeable librarian who helps with rulebook questions",
  "writing_style": "Clear and precise, citing page numbers for rules",
  "job": "librarian",
  "ontology_ids": [1, 2],
  "active": true
}
```

### Query a Librarian Agent

```http
POST /jobs/librarian/{agent_id}/query
Authorization: Bearer <user-token>
Content-Type: application/json

{
  "query": "What are the mechanics for grappling in combat?",
  "mode": "both",
  "top_k": 10,
  "library_item_ids": null,
  "include_trace": false
}
```

**Request Parameters**:
- `query` (required) - The user's question (1-2000 characters)
- `mode` - Response mode: `"nl"`, `"context"`, or `"both"` (default: `"both"`)
- `top_k` - Number of PDF chunks to retrieve (default: from config, max: 50)
- `library_item_ids` - Optional list of library item IDs to search within
- `include_trace` - Include execution trace for debugging (default: false)

**Response** (200 OK):
```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "mode": "both",
  "query": "What are the mechanics for grappling in combat?",
  "answer": "According to the Player's Handbook (page 195), grappling follows these mechanics:\n\n1. **Initiate Grapple**: Use the Attack action to make a special melee attack. If able to make multiple attacks, this replaces one of them.\n\n2. **Contest Check**: The target must succeed on a Strength (Athletics) or Dexterity (Acrobatics) check contested by your Strength (Athletics) check.\n\n3. **Success**: The target is grappled (condition). The grappled creature's speed becomes 0.\n\n4. **Escape**: The grappled creature can use its action to escape by succeeding on the same contest.\n\nAdditionally, the Dungeon Master's Guide (page 271) clarifies that you can move a grappled creature with you, but your speed is halved unless the creature is two or more sizes smaller than you.",
  "chunks": [
    {
      "library_item_id": 1,
      "page_number": 195,
      "text": "Grappling\nWhen you want to grab a creature or wrestle with it, you can use the Attack action to make a special melee attack, a grapple...",
      "score": 0.89
    },
    {
      "library_item_id": 2,
      "page_number": 271,
      "text": "Moving a Grappled Creature\nWhen you move, you can drag or carry the grappled creature with you, but your speed is halved...",
      "score": 0.76
    }
  ],
  "library_items_used": [1, 2],
  "trace": null
}
```

### PDF Embedding Endpoints

#### Trigger Embedding

```http
POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding
Authorization: Bearer <admin-or-world-builder-token>
```

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

**Response**:
```json
[
  {
    "job_id": 42,
    "ontology_id": 1,
    "status": "done",
    "progress": 1.0,
    "description": "Embedding PDF book (library item 5)",
    "started_at": "2025-10-29T12:00:00Z",
    "completed_at": "2025-10-29T12:05:30Z",
    "duration_seconds": 330.5,
    "error_message": null,
    "details": "{\"chunks_created\": 320, \"chunks_failed\": 0, \"total_pages\": 320}"
  }
]
```

### Best Practices

#### Agent Configuration

1. **Name**: Use descriptive names (e.g., "Rules Librarian", "Lore Keeper")
2. **Writing Style**: Emphasize accuracy and citation format
3. **Ontologies**: Link to ontologies containing relevant library items
4. **Active Status**: Set `active: false` during PDF embedding

#### Query Optimization

1. **Specific Questions**: Better results with focused, specific questions
2. **Use library_item_ids**: Filter to specific books when known
3. **Adjust top_k**: Increase for complex topics spanning multiple pages
4. **Enable Tracing**: Use during development to debug retrieval

#### PDF Management

1. **Embed Immediately**: Embed PDFs right after upload
2. **Monitor Jobs**: Check embedding-jobs endpoint for progress
3. **Re-embed on Updates**: Re-trigger embedding if PDF is replaced
4. **Organize by Ontology**: Group related books in same ontology

### Error Responses

#### Agent Not Found
```json
{
  "detail": "Agent not found"
}
```
Status: 404

#### Agent Inactive
```json
{
  "detail": "Agent is not active"
}
```
Status: 400

#### Invalid Job Type
```json
{
  "detail": "Agent job type 'elder' is not 'librarian'"
}
```
Status: 400

#### No Embedded Books
```json
{
  "answer": "I couldn't find any relevant information in the available books to answer your question."
}
```
Status: 200 (with empty chunks)

### Performance

Expected latency (varies by query complexity):
- Simple queries: 2-5 seconds
- Complex queries: 5-10 seconds
- First query (cold start): +2-3 seconds for model loading

Factors affecting performance:
- Number of embedded PDFs in ontology
- Top-k setting (higher = more retrieval)
- LLM model speed
- Neo4j vector index performance
