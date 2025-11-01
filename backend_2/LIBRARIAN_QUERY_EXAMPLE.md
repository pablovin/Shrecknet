# Librarian Query Endpoint Example

## Overview

The Librarian query endpoint (`POST /jobs/librarian/{agent_id}/query`) searches through embedded PDF books and returns relevant chunks with complete source metadata, including book title, authors, and page information.

## Endpoint

```
POST /jobs/librarian/{agent_id}/query
```

## Authentication

Requires Bearer token authentication.

## Example Request

```json
{
  "query": "What are the main principles of world building?",
  "mode": "both",
  "top_k": 5,
  "library_item_ids": null,
  "include_trace": false,
  "score_threshold": 0.3
}
```

### Request Parameters

- **query** (string, required): The search query (1-2000 characters)
- **mode** (string, optional): Response mode
  - `"nl"`: Natural language answer only
  - `"context"`: Context chunks only
  - `"both"`: Both answer and context chunks (default)
- **top_k** (integer, optional): Number of chunks to retrieve (1-50, default from config)
- **library_item_ids** (array of integers, optional): Filter search to specific library items
- **include_trace** (boolean, optional): Include execution trace for debugging (default: false)
- **score_threshold** (float, optional): Minimum similarity score threshold (0.0-1.0, default: 0.3)

## Example Response

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "mode": "both",
  "query": "What are the main principles of world building?",
  "answer": "World building requires attention to several key principles<sup class=\"src\" data-item=\"42\" data-page=\"15\" data-url=\"http://localhost:8000/media/library/1/42/content.pdf#page=15\">[page 15]</sup>. First, consistency is crucial - the rules of your world must remain stable throughout<sup class=\"src\" data-item=\"42\" data-page=\"16\" data-url=\"http://localhost:8000/media/library/1/42/content.pdf#page=16\">[page 16]</sup>. Second, depth matters - even if readers don't see all the details, a well-developed world feels authentic<sup class=\"src\" data-item=\"87\" data-page=\"23\" data-url=\"http://localhost:8000/media/library/1/87/content.pdf#page=23\">[page 23]</sup>.",
  "chunks": [
    {
      "library_item_id": 42,
      "page_number": 15,
      "text": "The foundation of world building rests on three pillars: consistency, depth, and believability. A world must follow its own internal logic, even when that logic differs from our reality...",
      "score": 0.89,
      "pdf_url": "http://localhost:8000/media/library/1/42/content.pdf",
      "page_url": "http://localhost:8000/media/library/1/42/content.pdf#page=15",
      "book_title": "The Art of World Building",
      "book_authors": "Jane Worldsmith, John Fictional"
    },
    {
      "library_item_id": 42,
      "page_number": 16,
      "text": "Consistency means that once you establish a rule - whether about magic, technology, or social structures - you must maintain it. Breaking your own rules without justification will confuse readers...",
      "score": 0.85,
      "pdf_url": "http://localhost:8000/media/library/1/42/content.pdf",
      "page_url": "http://localhost:8000/media/library/1/42/content.pdf#page=16",
      "book_title": "The Art of World Building",
      "book_authors": "Jane Worldsmith, John Fictional"
    },
    {
      "library_item_id": 87,
      "page_number": 23,
      "text": "Creating depth doesn't mean you need to document every detail before writing. Instead, develop systems that can generate consistent details on demand. A well-designed cultural framework...",
      "score": 0.82,
      "pdf_url": "http://localhost:8000/media/library/1/87/content.pdf",
      "page_url": "http://localhost:8000/media/library/1/87/content.pdf#page=23",
      "book_title": "Fictional Cultures: A Guide",
      "book_authors": "Sarah Culturalist"
    },
    {
      "library_item_id": 42,
      "page_number": 17,
      "text": "Believability emerges from the intersection of consistency and depth. When readers can predict how your world will respond to new situations based on established patterns...",
      "score": 0.79,
      "pdf_url": "http://localhost:8000/media/library/1/42/content.pdf",
      "page_url": "http://localhost:8000/media/library/1/42/content.pdf#page=17",
      "book_title": "The Art of World Building",
      "book_authors": "Jane Worldsmith, John Fictional"
    },
    {
      "library_item_id": 87,
      "page_number": 24,
      "text": "Research real-world analogues for your fictional societies. Even fantasy worlds benefit from understanding how actual cultures develop, trade, and interact...",
      "score": 0.76,
      "pdf_url": "http://localhost:8000/media/library/1/87/content.pdf",
      "page_url": "http://localhost:8000/media/library/1/87/content.pdf#page=24",
      "book_title": "Fictional Cultures: A Guide",
      "book_authors": "Sarah Culturalist"
    }
  ],
  "library_items_used": [42, 87],
  "trace": null
}
```

## Response Fields

### Root Level

- **agent_id** (string): ID of the librarian agent that processed the query
- **mode** (string): Response mode that was used
- **query** (string): The original query
- **answer** (string, nullable): Natural language answer with inline citations (when mode is "nl" or "both")
- **chunks** (array): Array of retrieved chunks with metadata (when mode is "context" or "both")
- **library_items_used** (array of integers): Unique list of library item IDs referenced in the response
- **trace** (array, nullable): Execution trace for debugging (only when include_trace=true)

### Chunk Object

Each chunk in the `chunks` array contains:

- **library_item_id** (integer): Database ID of the library item (book)
- **page_number** (integer): Page number within the book
- **text** (string): The extracted text chunk
- **score** (float): Similarity score (0.0-1.0, higher is more relevant)
- **pdf_url** (string, nullable): Direct URL to the PDF file
- **page_url** (string, nullable): URL to the specific page in the PDF
- **book_title** (string, nullable): Title of the book
- **book_authors** (string, nullable): Authors of the book

## Frontend Integration

The enhanced source metadata allows the frontend to:

1. **Display rich citations**: Show "Source: The Art of World Building by Jane Worldsmith, page 15" instead of just "Source: item #42, page 15"

2. **Build a sources section**: Create a formatted bibliography with all referenced books

3. **Navigate to sources**: Use `pdf_url` and `page_url` to link directly to source pages

4. **Group sources by book**: Use `library_item_id` and `book_title` to organize multiple citations from the same book

## Example cURL Request

```bash
curl -X POST "http://localhost:8000/jobs/librarian/550e8400-e29b-41d4-a716-446655440000/query" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the main principles of world building?",
    "mode": "both",
    "top_k": 5
  }'
```

## Error Responses

### 404 Not Found
```json
{
  "detail": "Agent not found"
}
```

### 400 Bad Request
```json
{
  "detail": "Agent is not active"
}
```

or

```json
{
  "detail": "Agent job type 'elder' is not 'librarian'"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Librarian query execution failed: [error details]"
}
```

## Notes

- The answer includes inline citations using `<sup>` tags with data attributes that can be used by the frontend to render clickable footnotes
- The `chunks` array is sorted by relevance score (highest first)
- Book metadata (title and authors) will be null if the library item cannot be found in the database
- The `library_items_used` field provides a quick way to identify all unique books referenced without parsing the chunks array
