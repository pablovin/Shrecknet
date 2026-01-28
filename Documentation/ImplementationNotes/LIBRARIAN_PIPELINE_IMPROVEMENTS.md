# Librarian Query Pipeline Improvements - Summary

This document summarizes the changes made to the librarian query pipeline.

## Overview

The librarian query pipeline has been enhanced with:
1. Automatic subquery generation for better information retrieval
2. Parallel querying across main query and subqueries
3. Improved citation format with full source metadata
4. Tracking of sources actually used in answers
5. Better PDF page mapping

## Key Changes

### 1. Subquery Generation

**File:** `app/jobs/librarian/librarian.py`

- Added `_generate_subqueries()` method that uses LLM to create up to 4 focused subqueries
- Subqueries help retrieve more comprehensive context for complex questions
- Uses book metadata (titles) to guide subquery generation

**Prompt:** `app/jobs/librarian/prompts.py`
- Added `SUBQUERY_GENERATION_PROMPT` for LLM-based subquery generation

### 2. Parallel Retrieval

**File:** `app/jobs/librarian/librarian.py`

- Added `_retrieve_chunks_for_queries()` method for parallel retrieval
- Uses `asyncio.gather()` to query all queries simultaneously
- Deduplicates results by (library_item_id, page_number)
- Significantly faster for complex queries

### 3. Enhanced Citation Format

**Previous format:**
```html
<sup class="src" data-item="42" data-page="15" data-url="...">
```

**New format:**
```html
<sub library_item_id="42" library_item_name="The Art of Game Mastering" page="15">
```

**Benefits:**
- Book title embedded directly in citation
- Easier to parse in frontend
- Self-contained (no need to look up book title separately)
- ALL mentions are cited, not just first occurrence

**Implementation:**
- Updated `COMBINED_ANSWER_STYLE_PROMPT` to instruct LLM to use new format
- LLM generates citations directly in the new format

### 4. Sources Tracking

**File:** `app/jobs/librarian/schemas.py`

Added `sources_used` field to `LibrarianQueryResponse`:
- Contains only chunks actually cited in the answer
- Extracted using regex pattern matching on `<sub>` tags
- Enables frontend to show "Sources" section with only referenced books

**Implementation:**
- Added `_extract_sources_from_answer()` method
- Parses answer text for citation tags
- Filters chunks to match cited sources

### 5. PDF Page Mapping Fix

**File:** `app/services/pdf_embedding_service.py`

**Issue:** PyMuPDF (fitz) wasn't using page labels, causing incorrect page numbers

**Fix:**
- Added page label extraction for PyMuPDF
- Falls back to 1-indexed page numbers if labels unavailable
- Better error handling for non-numeric page labels
- Consistent behavior across PyPDF2 and PyMuPDF

### 6. Schema Updates

**File:** `app/jobs/librarian/schemas.py`

Added to `LibrarianQueryResponse`:
- `subqueries: list[str]` - Generated subqueries
- `sources_used: list[RetrievedChunk]` - Actually cited sources

## New Response Structure

```json
{
  "agent_id": "...",
  "mode": "both",
  "query": "original question",
  "subqueries": ["sub 1", "sub 2", ...],
  "answer": "text with <sub library_item_id=\"42\" library_item_name=\"Book\" page=\"15\">",
  "chunks": [...],  // All retrieved chunks
  "sources_used": [...],  // Only cited chunks
  "library_items_used": [42, 15],
  "trace": null
}
```

## Pipeline Flow

1. **Receive query** from user
2. **Fetch library metadata** to understand available books
3. **Generate subqueries** (up to 4) using LLM
4. **Parallel retrieval**: Query main + subqueries simultaneously
5. **Deduplicate & rank** results by relevance score
6. **Generate answer** with proper `<sub>` citations
7. **Extract sources** actually used from answer
8. **Return response** with all metadata

## Testing

Added tests in `tests/test_librarian.py`:
- Schema validation for new fields
- Subqueries list handling
- Sources_used array validation

## Documentation

Updated `LIBRARIAN_QUERY_EXAMPLE.md`:
- New citation format documentation
- Subqueries explanation
- Sources_used vs chunks distinction
- Frontend integration examples

Created `LIBRARIAN_QUERY_OUTPUT_EXAMPLE.json`:
- Complete example response
- Shows realistic subqueries
- Demonstrates citation format
- Illustrates sources_used

## Migration Notes

### Frontend Changes Needed

1. **Parse new citation format:**
   ```javascript
   const regex = /<sub\s+library_item_id="(\d+)"\s+library_item_name="([^"]+)"\s+page="(\d+)">/g;
   ```

2. **Use sources_used for bibliography:**
   ```javascript
   const bibliography = response.sources_used.map(source => ({
     title: source.book_title,
     page: source.page_number,
     url: source.page_url
   }));
   ```

3. **Display subqueries** (optional):
   ```javascript
   if (response.subqueries.length > 0) {
     // Show "Related questions explored: ..."
   }
   ```

### Backward Compatibility

- Old fields (`chunks`, `library_items_used`) still present
- New fields have sensible defaults (empty arrays)
- API endpoint unchanged
- Request schema unchanged

## Performance Impact

- **Improved**: Parallel retrieval reduces latency for multi-ontology searches
- **Improved**: Subqueries provide better context coverage
- **Trade-off**: One extra LLM call for subquery generation (~0.5s)
- **Overall**: Better quality answers with similar or better performance

## Future Enhancements

Potential improvements:
1. Cache subqueries for similar questions
2. Allow users to see/edit subqueries before retrieval
3. Show which subquery retrieved each chunk
4. Adaptive number of subqueries based on question complexity
5. Multi-language support for subquery generation
