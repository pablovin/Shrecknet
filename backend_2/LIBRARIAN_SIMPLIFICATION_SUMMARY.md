# Librarian Query Job Simplification Summary

## Overview

The librarian query job has been simplified to provide a more straightforward and efficient workflow for retrieving and answering questions from embedded PDF library items.

## Changes Made

### 1. Removed Subquery Generation
- **Before**: The system generated up to 4 focused subqueries to improve retrieval
- **After**: Direct retrieval using only the main user query
- **Benefit**: Reduces LLM calls, simplifies the pipeline, and makes it more predictable

### 2. Simplified Retrieval Process
- **Before**: Parallel retrieval across main query + subqueries with complex deduplication
- **After**: Direct retrieval of top K chunks across all library items in the ontology
- **Benefit**: Faster execution, easier to understand and debug

### 3. Streamlined Code
- **Removed Methods**:
  - `_generate_subqueries()` - Generated subqueries from the main query
  - `_retrieve_chunks_for_queries()` - Parallel retrieval for multiple queries
  - `_get_library_items_for_ontologies()` - No longer needed with simplified flow
  - `_generate_combined_answer()` - Unused alternative answer generation
  - `_generate_answer()` - Unused alternative answer generation
  - `_generate_single_pass_answer()` - Unused alternative answer generation
  - `_apply_style()` - Unused separate style application
  - Several utility methods no longer needed

- **Kept Methods**:
  - `execute()` - Main pipeline (simplified)
  - `_retrieve_chunks()` - Retrieves chunks from Neo4j embeddings
  - `_fetch_library_metadata()` - Gets book titles and authors
  - `_generate_answer_with_style()` - Single-pass answer with citations
  - `_extract_sources_from_answer()` - Parses citations from answer

### 4. Response Format Unchanged
- **Maintained**: `<sub>` citation tags with library_item_id, library_item_name, and page
- **Maintained**: Sources tracking and library_items_used
- **Changed**: `subqueries` field now always returns empty array `[]`

## New Workflow

1. **User submits query** to `/jobs/librarian/{agent_id}/query`
2. **Retrieve chunks**: Get top K chunks across all library items in the agent's ontologies
3. **Fetch metadata**: Get book titles and authors for the retrieved chunks
4. **Generate answer**: Single LLM call to generate answer with citations and apply writing style
5. **Extract sources**: Parse `<sub>` tags to identify which chunks were actually cited
6. **Return response**: Answer, chunks, sources_used, and library_items_used

## Code Size Reduction

- **Before**: 732 lines
- **After**: 381 lines
- **Reduction**: 351 lines (48% smaller)

## Compatibility

### Fully Compatible
- ✅ Neo4j vector embeddings and search
- ✅ PDF embedding service
- ✅ Citation format with `<sub>` tags
- ✅ Response schema (LibrarianQueryResponse)
- ✅ All existing library items and embeddings
- ✅ Frontend integration (ignores empty subqueries array)

### Changed
- ⚠️ `subqueries` field in response is now always `[]`
- ⚠️ No longer generates or uses subqueries for retrieval

## Testing

All existing tests have been updated to reflect the simplified pipeline:
- Schema validation tests pass
- Empty subqueries array is expected
- Citation format tests unchanged

## Performance Benefits

1. **Reduced LLM calls**: One fewer call per query (no subquery generation)
2. **Faster retrieval**: Single direct query instead of parallel multi-query retrieval
3. **Simpler debugging**: Fewer moving parts to troubleshoot
4. **Lower costs**: Fewer tokens used per query

## Migration Notes

No migration needed. The simplified version is backward compatible with:
- Existing Neo4j embeddings
- Existing library items
- Existing agent configurations
- Frontend code (empty subqueries array is valid)

The only visible change to end users is that the `subqueries` field in responses will be empty.
