# Admin User Notes API Endpoints

This document describes the admin-specific endpoints for creating and managing user notes on behalf of other users.

## Admin Note Creation Endpoint

### POST /admin/user_notes/

**Description:** Allows system administrators to create notes on behalf of any user and optionally share them with other users.

**Authentication:** Required - System Admin role

**Request Body:**
```json
{
  "title": "Note Title",
  "content": "Note content",
  "note_date": "2024-01-01T12:00:00Z",  // Optional
  "tags": ["tag1", "tag2"],  // Optional, default: []
  "gameworld_id": 1,  // Optional
  "author_user_id": 123,  // Required - ID of the user who will be the note's author
  "shared_with_user_ids": [456, 789],  // Optional - IDs of users to share the note with
  "contributors": null,  // Optional
  "locked_by_user_id": null,  // Optional
  "locked_at": null  // Optional
}
```

**Response:**
```json
{
  "id": 1,
  "user_id": 123,
  "title": "Note Title",
  "content": "Note content",
  "note_date": "2024-01-01T12:00:00Z",
  "tags": ["tag1", "tag2"],
  "gameworld_id": 1,
  "shared_with_user_ids": [456, 789],
  "contributors": null,
  "locked_by_user_id": null,
  "locked_at": null,
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": null
}
```

**Status Codes:**
- `200 OK`: Note created successfully
- `401 Unauthorized`: Authentication required
- `403 Forbidden`: User does not have system admin role
- `404 Not Found`: Author user or one of the shared users does not exist
- `422 Unprocessable Entity`: Invalid request body

**Example Usage:**

```bash
# Create a note as admin for user 123
curl -X POST "http://localhost:8000/admin/user_notes/" \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Project Planning Notes",
    "content": "Initial planning for the new campaign",
    "author_user_id": 123,
    "shared_with_user_ids": [456, 789],
    "tags": ["planning", "campaign"]
  }'
```

## Key Features

1. **Author Specification**: Admin can specify which user will be the author of the note via `author_user_id`
2. **Shared Notes**: Admin can share the note with multiple users via `shared_with_user_ids`
3. **Validation**: The endpoint validates that:
   - The author user exists
   - All users in `shared_with_user_ids` exist
4. **Access Control**: Only system administrators can use this endpoint

## Existing User Note Endpoints (for reference)

These endpoints work on behalf of the authenticated user:

- `POST /user_notes/` - Create a note (author is the authenticated user)
- `GET /user_notes/` - List notes for the authenticated user
- `GET /user_notes/{note_id}` - Get a specific note
- `PATCH /user_notes/{note_id}` - Update a note
- `DELETE /user_notes/{note_id}` - Delete a note

## Permissions

- **Regular users** can only create notes for themselves and manage their own notes
- **System admins** can:
  - Use all regular endpoints
  - Use the `/admin/user_notes/` endpoint to create notes for any user
  - Specify who the note is shared with during creation
