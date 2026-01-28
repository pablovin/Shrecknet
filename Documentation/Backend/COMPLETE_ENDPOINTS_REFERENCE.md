# Complete API Endpoints Documentation

## User Notes Endpoints (All Users)

### 1. Create Note (as current user)
**POST /user_notes/**
- Authentication: Required (any authenticated user)
- Creates a note where the authenticated user is the author
- Request Body:
  ```json
  {
    "title": "My Note",
    "content": "Note content",
    "tags": ["tag1", "tag2"],
    "gameworld_id": 1,
    "shared_with_user_ids": [2, 3],
    "note_date": "2024-01-01T12:00:00Z"
  }
  ```

### 2. List Notes
**GET /user_notes/**
- Authentication: Required
- Returns notes where user is author or shared with
- Query Parameters:
  - `search`: Search in title/content
  - `start_date`: Filter by date range
  - `end_date`: Filter by date range

### 3. Get Note
**GET /user_notes/{note_id}**
- Authentication: Required
- Returns note if user is author or shared with

### 4. Update Note
**PATCH /user_notes/{note_id}**
- Authentication: Required
- Updates note (must be author or shared user)
- Request Body (all optional):
  ```json
  {
    "title": "Updated Title",
    "content": "Updated content",
    "tags": ["new", "tags"],
    "shared_with_user_ids": [4, 5]
  }
  ```

### 5. Delete Note
**DELETE /user_notes/{note_id}**
- Authentication: Required
- Deletes note (must be author)

---

## Admin Notes Endpoints (System Admin Only)

### 1. Create Note on Behalf of User (NEW)
**POST /admin/user_notes/**
- Authentication: Required (System Admin role)
- Creates a note on behalf of any user
- Request Body:
  ```json
  {
    "title": "Note Title",
    "content": "Note content",
    "author_user_id": 123,
    "shared_with_user_ids": [456, 789],
    "tags": ["tag1", "tag2"],
    "gameworld_id": 1,
    "note_date": "2024-01-01T12:00:00Z"
  }
  ```
- Response: Returns the created note
- Validations:
  - `author_user_id` must exist (404 if not)
  - All `shared_with_user_ids` must exist (404 if not)
  - User must have system admin role (403 if not)

---

## Complete Endpoint Summary Table

| Method | Endpoint | Access | Purpose |
|--------|----------|--------|---------|
| POST | `/user_notes/` | Any user | Create note as self |
| GET | `/user_notes/` | Any user | List own/shared notes |
| GET | `/user_notes/{id}` | Any user | Get note details |
| PATCH | `/user_notes/{id}` | Any user | Update note |
| DELETE | `/user_notes/{id}` | Any user | Delete note |
| **POST** | **`/admin/user_notes/`** | **Admin only** | **Create note for any user** |

---

## Example Usage Scenarios

### Scenario 1: Admin creates a note for a user
```bash
curl -X POST "http://localhost:8000/admin/user_notes/" \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Campaign Notes",
    "content": "Initial world building ideas",
    "author_user_id": 5,
    "shared_with_user_ids": [],
    "tags": ["campaign", "worldbuilding"]
  }'
```

### Scenario 2: Admin creates a shared note for team collaboration
```bash
curl -X POST "http://localhost:8000/admin/user_notes/" \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Team Meeting Notes",
    "content": "Discussion about new features",
    "author_user_id": 5,
    "shared_with_user_ids": [6, 7, 8],
    "tags": ["meeting", "planning"]
  }'
```

### Scenario 3: Regular user creates a note
```bash
curl -X POST "http://localhost:8000/user_notes/" \
  -H "Authorization: Bearer <user_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "My Personal Note",
    "content": "Private thoughts",
    "shared_with_user_ids": [9],
    "tags": ["personal"]
  }'
```

---

## Security Notes

1. **Admin endpoint** (`/admin/user_notes/`) requires system admin role
2. **Regular endpoints** (`/user_notes/*`) require authentication but work for all roles
3. **Non-admins cannot** create notes on behalf of others
4. **All users** are validated before note creation
5. **Shared users** can edit notes but not change sharing settings (unless they're the author)
