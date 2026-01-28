# Import Endpoints - Implementation Summary

## What Was Implemented

Three new REST API endpoints have been added to backend_2 to import data from the old backend structure:

### 1. POST /imports/users
Imports user data from the old backend to backend_2.

**Data imported:**
- ✅ Usernames (from nickname field)
- ✅ Hashed passwords (preserved as-is)
- ✅ Full names (uses nickname as full_name)
- ✅ Timezones
- ✅ Roles (with proper mapping)
- ❌ Images (excluded per requirements)

**Role Mapping:**
- `system admin` → `ADMIN`
- `world builder` → `WORLD_BUILDER`
- `writer` → `WRITER`
- `player` → `PLAYER`

### 2. POST /imports/game-tables
Imports game tables from the old backend to backend_2 as Games.

**Data imported:**
- ✅ Table name → Game name
- ✅ All table members
- ✅ Automatic assignment to ontology
- ❌ Crest images (excluded per requirements)

**Notes:**
- If no ontology exists, creates a default "Imported Games" ontology
- Members are properly linked to the game

### 3. POST /imports/sessions
Imports game sessions from the old backend to backend_2.

**Data imported:**
- ✅ Session title
- ✅ Scheduled date (ONLY sessions with final schedule)
- ✅ Location
- ✅ Summary
- ✅ All attendees with attendance status
- ❌ Polls (excluded per requirements)

**Filter Criteria:**
- Only imports sessions that have a `scheduled_time` (final schedule)
- Sessions without a scheduled time are skipped

## Key Features

### Security
- All endpoints require admin authentication
- Non-admin users receive 403 Forbidden
- Unauthenticated requests receive 401 Unauthorized

### Idempotency
- Safe to run multiple times
- Duplicate detection based on:
  - Users: email address
  - Games: name
  - Sessions: game_id + title
- Second run shows `imported: 0`, `skipped: N`

### Console Feedback
Detailed logging for each operation:
```
INFO | Starting user import process
INFO | Found 4 users in old database
INFO | Imported user: admin@test.com
INFO | Imported user: builder@test.com
INFO | User import completed: 4 imported, 0 skipped, 0 errors
```

### Error Handling
- Individual record errors don't stop the import
- Errors are logged with details
- Final response includes error count
- Fatal errors (e.g., database unavailable) return 500

### Data Integrity
- ❌ Does not modify old backend models
- ❌ Does not change old database data
- ✅ Only writes to new backend_2 database
- ✅ Preserves relationships (members, attendees)
- ✅ Handles missing references gracefully

## Configuration

```bash
# Set old database location (optional)
export OLD_DATABASE_URL="sqlite+aiosqlite:///path/to/prod.db"

# Default: ./data/prod.db (relative to application root)
```

## Usage Order

**Important:** Import in this order to maintain referential integrity:

1. Import users first
2. Import game tables second (requires users)
3. Import sessions last (requires users and games)

## Example Usage

```bash
# Get admin token
TOKEN=$(curl -X POST http://localhost:8000/auth/token \
  -d "username=admin&password=yourpassword" \
  | jq -r .access_token)

# Import in correct order
curl -X POST http://localhost:8000/imports/users \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://localhost:8000/imports/game-tables \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://localhost:8000/imports/sessions \
  -H "Authorization: Bearer $TOKEN"
```

## Response Format

All endpoints return:
```json
{
  "message": "Import type import completed",
  "imported": 10,
  "skipped": 0,
  "errors": 0
}
```

## Files Changed

- `backend_2/app/api/routers/imports.py` - New router with import endpoints
- `backend_2/app/api/routers/__init__.py` - Register imports router
- `backend_2/app/api/deps.py` - Add `get_current_admin_user` dependency
- `backend_2/IMPORT_DOCUMENTATION.md` - Full API documentation
- `backend_2/tests/test_imports.py` - Integration tests

## Testing

Sample data creation script provided at `/tmp/test_imports.py`

Comprehensive test suite at `backend_2/tests/test_imports.py`

## Security Scan Results

✅ CodeQL security scan: 0 vulnerabilities found
✅ No security issues detected
