# Librarian Query Endpoint Example

## Overview

The Librarian query endpoint (`POST /jobs/librarian/{agent_id}/query`) searches through embedded PDF books and returns relevant chunks with complete source metadata, including book title, authors, and page information.

**New Features:**
- Automatic generation of up to 4 focused subqueries to improve retrieval
- Parallel retrieval across main query and subqueries
- Enhanced citation format using `<sub>` tags with library_item_id, library_item_name, and page
- Tracking of sources actually used in the answer
- Improved PDF page mapping for accurate citations

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

**Note:** The answer now uses `<sub>` tags instead of `<sup>` tags for better citation tracking. See [LIBRARIAN_QUERY_OUTPUT_EXAMPLE.json](./LIBRARIAN_QUERY_OUTPUT_EXAMPLE.json) for a complete example.

```json
{
  "agent_id": "550e8400-e29b-41d4-a716-446655440000",
  "mode": "both",
  "query": "What are the main principles of world building?",
  "subqueries": [
    "What makes a world setting consistent?",
    "How do you create depth in fictional worlds?",
    "What are best practices for world believability?"
  ],
  "answer": "World building requires attention to several key principles. First, consistency is crucial<sub library_item_id=\"42\" library_item_name=\"The Art of World Building\" page=\"15\">. The rules of your world must remain stable throughout<sub library_item_id=\"42\" library_item_name=\"The Art of World Building\" page=\"16\">. Second, depth matters<sub library_item_id=\"87\" library_item_name=\"Fictional Cultures: A Guide\" page=\"23\">...",
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
- **subqueries** (array of strings): Up to 4 generated subqueries used to improve retrieval
- **answer** (string, nullable): Natural language answer with inline `<sub>` citations (when mode is "nl" or "both")
- **chunks** (array): Array of all retrieved chunks with metadata (when mode is "context" or "both")
- **sources_used** (array): Array of chunks that were actually cited in the answer
- **library_items_used** (array of integers): Unique list of library item IDs referenced in the response
- **trace** (array, nullable): Execution trace for debugging (only when include_trace=true)

### Chunk Object

Each chunk in the `chunks` and `sources_used` arrays contains:

- **library_item_id** (integer): Database ID of the library item (book)
- **page_number** (integer): Page number within the book
- **text** (string): The extracted text chunk
- **score** (float): Similarity score (0.0-1.0, higher is more relevant)
- **pdf_url** (string, nullable): Direct URL to the PDF file
- **page_url** (string, nullable): URL to the specific page in the PDF
- **book_title** (string, nullable): Title of the book
- **book_authors** (string, nullable): Authors of the book

### Citation Format

The answer uses `<sub>` tags for citations with three required attributes:

```html
<sub library_item_id="42" library_item_name="The Art of World Building" page="15">
```

- **library_item_id**: Database ID of the source book
- **library_item_name**: Title of the source book
- **page**: Page number where the information is found

**Important:** Citations are added for ALL mentions of information from a source, not just the first mention.

## Frontend Integration

The enhanced source metadata and citation format allow the frontend to:

1. **Display rich citations**: Parse `<sub>` tags to show "Source: The Art of World Building, page 15" with the book title embedded in the citation

2. **Build a sources section**: Use the `sources_used` array to create a formatted bibliography showing only sources actually cited in the answer

3. **Navigate to sources**: Extract `pdf_url` and `page_url` from chunks to link directly to source pages

4. **Group sources by book**: Use `library_item_id` and `book_title` to organize multiple citations from the same book

5. **Track subqueries**: Display the generated subqueries to show users how the system approached their question

### Parsing Citations

Frontend code can parse `<sub>` tags like this:

```javascript
// Example: Extract citations from answer
const citationRegex = /<sub\s+library_item_id="(\d+)"\s+library_item_name="([^"]+)"\s+page="(\d+)">/g;
const citations = [];
let match;

while ((match = citationRegex.exec(answer)) !== null) {
  citations.push({
    itemId: parseInt(match[1]),
    bookName: match[2],
    page: parseInt(match[3])
  });
}

// Use sources_used to get full chunk details for each citation
```

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

### Changes from Previous Version

- **NEW**: Automatic subquery generation (up to 4 subqueries) for better information retrieval
- **NEW**: Parallel retrieval across main query and subqueries
- **CHANGED**: Citation format from `<sup>` tags to `<sub>` tags with library_item_id, library_item_name, and page attributes
- **CHANGED**: Citations now appear for ALL mentions of a source, not just the first mention
- **NEW**: `sources_used` field contains only chunks actually cited in the answer
- **NEW**: `subqueries` field shows the generated subqueries
- **IMPROVED**: PDF page mapping now correctly handles both PyMuPDF and PyPDF2, including page labels

### Implementation Details

- The `chunks` array contains all retrieved relevant chunks (sorted by relevance score)
- The `sources_used` array contains only chunks that were cited in the answer (a subset of chunks)
- Book metadata (title and authors) will be null if the library item cannot be found in the database
- The `library_items_used` field provides a quick way to identify all unique books referenced
- Subquery generation is automatic and based on the question complexity and available books
- If a question is simple, fewer than 4 subqueries may be generated

### For Complete Example

See [LIBRARIAN_QUERY_OUTPUT_EXAMPLE.json](./LIBRARIAN_QUERY_OUTPUT_EXAMPLE.json) for a full, detailed example of the new response format.
