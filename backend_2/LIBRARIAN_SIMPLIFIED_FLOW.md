# Simplified Librarian Query Flow

## Visual Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│  User Request                                                   │
│  POST /jobs/librarian/{agent_id}/query                         │
│  { query, mode, top_k, library_item_ids, score_threshold }     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: Retrieve Chunks                                        │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ For each ontology in agent:                               │  │
│  │   - Generate query embedding using OpenAI                 │  │
│  │   - Search Neo4j vector index (pdf_chunk_text_vec_idx)   │  │
│  │   - Filter by ontology_id and library_item_ids (if any)  │  │
│  │   - Get top K chunks with score >= threshold             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Result: List of chunks with:                                  │
│  - library_item_id, page_number, text, score                  │
│  - pdf_url, page_url                                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: Fetch Library Metadata                                │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Query PostgreSQL LibraryItem table:                       │  │
│  │   - Get title and authors for each library_item_id       │  │
│  │   - Batch fetch all items in single query                │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Result: Chunks enriched with book_title and book_authors      │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: Generate Answer (if mode = "nl" or "both")            │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Single LLM call to GPT-4o:                                │  │
│  │   - Format chunks with book titles and page numbers       │  │
│  │   - Include agent writing style in prompt                 │  │
│  │   - Request citations using <sub> tags                    │  │
│  │   - Temperature: 0.3 (focused, grounded)                  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Result: Answer with <sub library_item_id="X"                  │
│          library_item_name="Y" page="Z"> citations             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4: Extract Sources Used                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Parse answer for <sub> tags:                              │  │
│  │   - Extract library_item_id and page from citations       │  │
│  │   - Match against retrieved chunks                        │  │
│  │   - Build sources_used list                               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  Result: List of chunks that were actually cited               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 5: Build Response                                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ LibrarianQueryResponse:                                    │  │
│  │   - agent_id                                               │  │
│  │   - mode                                                   │  │
│  │   - query                                                  │  │
│  │   - subqueries: []  (always empty)                        │  │
│  │   - answer (if mode = "nl" or "both")                     │  │
│  │   - chunks (if mode = "context" or "both")                │  │
│  │   - sources_used                                           │  │
│  │   - library_items_used                                     │  │
│  │   - trace (if include_trace=true)                         │  │
│  └───────────────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Return JSON Response                                           │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Neo4j Vector Index
- **Index Name**: `pdf_chunk_text_vec_idx`
- **Node Label**: `PdfChunk`
- **Embedding Property**: `text_embedding`
- **Similarity Function**: cosine

### 2. Database Tables
- **LibraryItem**: Stores book metadata (title, authors)
- **PdfChunk** (Neo4j): Stores embedded text chunks

### 3. LLM Integration
- **Embedding Model**: text-embedding-3-small (via OpenAI)
- **Answer Model**: gpt-4o
- **Single Pass**: Answer generation with style in one call

## Performance Characteristics

- **LLM Calls per Query**: 2 (1 for embedding, 1 for answer)
- **Database Queries**: 2 (1 Neo4j vector search, 1 PostgreSQL metadata)
- **Typical Response Time**: 2-5 seconds
- **Token Usage**: ~1500-3000 tokens per query (varies with chunk count)

## Error Handling

- No chunks found → Returns helpful message
- Missing library metadata → Uses fallback "Book #{id}"
- Neo4j connection issues → Propagates exception with context
- LLM timeout → Propagates exception with timeout info
