# Page Visits Search Feature

## Overview

A new search endpoint has been added to the page-visits API to allow flexible searching of page visit statistics by `page_key`, `page_alias`, or `ontology_instance_id`.

## Problem Statement

Previously, the page-visits endpoint only supported exact matches by `page_key` via the path parameter:
```
GET /page-visits/pages/{page_key}/stats
```

This was problematic because:
1. Users might not know the exact `page_key` value
2. Page keys might be stored as aliases or ontology instance IDs
3. No way to search across multiple pages or use pattern matching

## Solution

Added a new search endpoint that allows flexible, case-insensitive pattern matching:
```
GET /page-visits/pages/search
```

### Query Parameters

All parameters are optional, but at least one must be provided:

- `page_key` (string, optional): Search by page_key pattern (case-insensitive substring match)
- `page_alias` (string, optional): Search by page_alias pattern (case-insensitive substring match)
- `ontology_instance_id` (string, optional): Search by ontology instance ID (case-insensitive substring match)
- `limit` (integer, optional, default=100): Maximum number of recent visits to include per page

### Example Usage

#### Search by page_key pattern
```bash
GET /page-visits/pages/search?page_key=character
```
Returns stats for all pages with "character" in their page_key (e.g., "character-john", "my-character-123")

#### Search by page_alias
```bash
GET /page-visits/pages/search?page_alias=wizard
```
Returns stats for all pages with "wizard" in their key

#### Search by ontology_instance_id
```bash
GET /page-visits/pages/search?ontology_instance_id=abc-123
```
Returns stats for all pages with "abc-123" in their key

#### Combine multiple criteria
```bash
GET /page-visits/pages/search?page_key=character&page_alias=hero
```
Returns stats for pages matching either "character" OR "hero"

### Response

Returns an array of `PageVisitStatsRead` objects:

```json
[
  {
    "page_key": "character-john-wizard",
    "total_visits": 42,
    "unique_users": 5,
    "last_visited_at": "2026-01-13T21:00:00Z",
    "recent_visits": [
      {
        "user_id": 1,
        "username": "player1",
        "visited_at": "2026-01-13T21:00:00Z"
      }
    ]
  }
]
```

## Implementation Details

### Architecture

The feature is implemented across three layers:

1. **Router** (`app/api/routers/page_visits.py`): New `search_page_stats` endpoint
2. **Service** (`app/services/page_visit_service.py`): New `search_page_keys` method
3. **Repository** (`app/repositories/page_visit_repository.py`): New `search_page_keys` method with SQL queries

### Database Queries

The repository uses SQLAlchemy's `ilike` for case-insensitive pattern matching:

```python
conditions.append(PageVisit.page_key.ilike(f"%{pattern}%"))
```

Multiple conditions are combined with `OR` logic, and distinct page_keys are returned.

### Security

The endpoint requires WORLD_BUILDER or ADMIN roles:
```python
dependencies=[Depends(require_roles(UserRole.WORLD_BUILDER, UserRole.ADMIN))]
```

## Testing

Test file created at `backend_2/tests/test_page_visits.py` with coverage for:
- Searching by page_key pattern
- Searching by page_alias pattern
- Searching by ontology_instance_id pattern
- Empty results when no parameters provided
- Existing exact match endpoint still works

## Backward Compatibility

The existing endpoint `/page-visits/pages/{page_key}/stats` remains unchanged and continues to work for exact matches. This is a purely additive change with no breaking modifications.

## Future Enhancements

Potential improvements for future iterations:
1. Add pagination support for large result sets
2. Support regex patterns instead of just substring matching
3. Add sorting options (by visit count, last visited, etc.)
4. Add filtering by date ranges
5. Add aggregation options (total across all matching pages)
