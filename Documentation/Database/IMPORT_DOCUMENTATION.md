# Import Endpoints Documentation

## Overview

The import endpoints allow migrating data from the old backend (backend) to the new backend_2 structure.

## Endpoints

All import endpoints require admin authentication.

### 1. Import Users
**POST** `/imports/users`

Imports users from the old backend database to the new backend_2 database.

**Imported fields:**
- `username` (from `nickname`)
- `email`
- `hashed_password`
- `full_name` (uses `nickname` since old backend doesn't have full_name)
- `timezone`
- `role` (mapped from old roles to new roles)

**Role mapping:**
- `system admin` → `ADMIN`
- `world builder` → `WORLD_BUILDER`
- `writer` → `WRITER`
- `player` → `PLAYER`

**Note:** User images are not imported.

**Response:**
```json
{
  "message": "User import completed",
  "imported": 4,
  "skipped": 0,
  "errors": 0
}
```

### 2. Import Game Tables
**POST** `/imports/game-tables`

Imports game tables from the old backend (Table) to the new backend_2 (Game).

**Prerequisites:**
- Users must be imported first
- An ontology must exist (one will be created automatically if none exists)

**Imported fields:**
- `name` (table name → game name)
- `ontology_id` (assigned to default ontology)
- `members` (from table members)

**Note:** Table crests/images are not imported.

**Response:**
```json
{
  "message": "Game table import completed",
  "imported": 2,
  "skipped": 0,
  "errors": 0
}
```

### 3. Import Sessions
**POST** `/imports/sessions`

Imports sessions from the old backend to the new backend_2.

**Prerequisites:**
- Users must be imported first
- Game tables must be imported first

**Import criteria:**
- **Only sessions with `scheduled_time` (final schedule) are imported**
- Polls are NOT imported (as specified in requirements)

**Imported fields:**
- `title` (from `name`)
- `scheduled_date` (from `scheduled_time`)
- `location`
- `summary`
- `attendees` (with their attendance status)

**Response:**
```json
{
  "message": "Session import completed",
  "imported": 3,
  "skipped": 0,
  "errors": 0
}
```

## Usage Example

### Using curl

```bash
# 1. Get authentication token (replace with your admin credentials)
TOKEN=$(curl -X POST http://localhost:8000/auth/token \
  -d "username=admin&password=yourpassword" \
  | jq -r .access_token)

# 2. Import users
curl -X POST http://localhost:8000/imports/users \
  -H "Authorization: Bearer $TOKEN"

# 3. Import game tables
curl -X POST http://localhost:8000/imports/game-tables \
  -H "Authorization: Bearer $TOKEN"

# 4. Import sessions
curl -X POST http://localhost:8000/imports/sessions \
  -H "Authorization: Bearer $TOKEN"
```

### Using Python

```python
import httpx

base_url = "http://localhost:8000"

# Get token
response = httpx.post(
    f"{base_url}/auth/token",
    data={"username": "admin", "password": "yourpassword"}
)
token = response.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Import users
response = httpx.post(f"{base_url}/imports/users", headers=headers)
print(response.json())

# Import game tables
response = httpx.post(f"{base_url}/imports/game-tables", headers=headers)
print(response.json())

# Import sessions
response = httpx.post(f"{base_url}/imports/sessions", headers=headers)
print(response.json())
```

## Configuration

The old database location can be configured using the `OLD_DATABASE_URL` environment variable:

```bash
export OLD_DATABASE_URL="sqlite+aiosqlite:///path/to/old/database.db"
```

Default: `sqlite+aiosqlite:///../backend/data/prod.db` (relative to backend directory)

## Idempotency

All import endpoints are idempotent - they can be run multiple times safely:
- Already imported records are skipped (matched by email for users, name for games/sessions)
- The second run will show `imported: 0` and `skipped: N` where N is the number previously imported

## Console Feedback

All import operations log detailed information to the console:
- Start of import process
- Number of records found in old database
- Each record imported/skipped
- Errors encountered
- Summary with counts (imported, skipped, errors)

Example output:
```
2025-10-28 22:29:03,071 | INFO | Starting user import process
2025-10-28 22:29:03,072 | INFO | Found 4 users in old database
2025-10-28 22:29:03,073 | INFO | Imported user: admin@test.com
2025-10-28 22:29:03,074 | INFO | Imported user: builder@test.com
2025-10-28 22:29:03,075 | INFO | Imported user: player1@test.com
2025-10-28 22:29:03,076 | INFO | Imported user: player2@test.com
2025-10-28 22:29:03,077 | INFO | User import completed: 4 imported, 0 skipped, 0 errors
```

## Error Handling

- Errors are logged for individual records but don't stop the import process
- The import continues with remaining records
- Error count is included in the response
- HTTP 500 errors are returned only for fatal errors (database connection failures, etc.)

## Security

- All endpoints require admin role (`UserRole.ADMIN`)
- Non-admin users will receive HTTP 403 Forbidden
- Unauthenticated requests will receive HTTP 401 Unauthorized
