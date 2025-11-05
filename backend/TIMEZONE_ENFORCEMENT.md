# Timezone Enforcement for Sessions, Polls, and Tables

## Summary

This update enforces timezone-aware datetimes for all session, poll, and table date/time fields to prevent timezone-related issues.

## Changes Made

### 1. Schema Validators (Input/Output)

Updated the following schemas to enforce timezone-aware datetimes:

- **`schema_session.py`**: Added validators to `SessionBase`, `SessionCreate`, `SessionRead`, and `SessionUpdate`
  - `scheduled_time`: Must include timezone info on create/update
  - `created_at`: Converted to UTC if naive on read (for backward compatibility)

- **`schema_session_poll.py`**: Added validators to `SessionPollCreate`, `SessionPollOptionRead`, and `SessionPollRead`
  - `proposed_times`: Must include timezone info on create
  - `proposed_time`: Converted to UTC if naive on read
  - `created_at`: Converted to UTC if naive on read

- **`schema_table.py`**: Added validators to `TableRead` and `TableListRead`
  - `created_at`: Converted to UTC if naive on read
  - `latest_session`, `next_session`: Converted to UTC if naive on read

### 2. Database Migration

Created migration `20251105_01_enforce_timezone_aware_datetimes.py` to:

- Convert all existing `session.scheduled_time` to Brussels timezone (Europe/Brussels)
- Convert all existing `sessionpolloption.proposed_time` to Brussels timezone
- Ensure all `created_at` fields have UTC timezone info

### 3. API Behavior

**Input (Create/Update):**
- All datetime fields in session and poll creation/updates MUST include timezone information
- Requests with naive datetimes (no timezone) will be rejected with HTTP 422 error
- Example valid format: `"2024-11-05T14:30:00+01:00"` (Brussels) or `"2024-11-05T13:30:00Z"` (UTC)
- Example invalid format: `"2024-11-05T14:30:00"` (no timezone)

**Output (Read):**
- All datetime fields in responses will include timezone information
- Format: ISO 8601 with timezone offset (e.g., `"2024-11-05T14:30:00+01:00"`)

### 4. Testing

Added comprehensive tests in `test_timezone_enforcement.py`:
- Test session creation with timezone-aware datetimes (pass)
- Test session creation with naive datetimes (fail)
- Test poll creation with timezone-aware datetimes (pass)
- Test poll creation with naive datetimes (fail)
- Test table listings return timezone-aware session times

## Migration Instructions

1. **Before deploying**: Ensure all applications creating sessions/polls are updated to send timezone-aware datetimes
2. **Deploy order**:
   - Deploy backend code
   - Run database migration: `alembic upgrade head`
3. **After migration**: All existing dates will be converted to timezone-aware format

## Example API Usage

### Creating a Session (Valid)
```json
{
  "name": "Game Night",
  "scheduled_time": "2024-11-15T19:00:00+01:00",
  "timezone": "Europe/Brussels",
  "summary": "Weekly game session",
  "location": "Discord",
  "attendee_ids": [1, 2, 3],
  "page_ids": []
}
```

### Creating a Session (Invalid - Will Fail)
```json
{
  "name": "Game Night",
  "scheduled_time": "2024-11-15T19:00:00",  // Missing timezone!
  "timezone": "Europe/Brussels",
  ...
}
```

### Creating a Poll (Valid)
```json
{
  "proposed_times": [
    "2024-11-15T19:00:00+01:00",
    "2024-11-16T19:00:00+01:00",
    "2024-11-17T19:00:00+01:00"
  ],
  "timezone": "Europe/Brussels"
}
```

## Backward Compatibility

- **Read operations**: Validators will automatically convert any remaining naive datetimes to UTC
- **Write operations**: Strict validation to ensure all new data has timezone info
- This ensures a smooth transition during the migration period

## Technical Details

- **Storage**: SQLite stores datetimes as ISO 8601 strings
- **Default timezone for existing data**: Europe/Brussels for scheduled times, UTC for created_at timestamps
- **Pydantic validators**: Used `mode="before"` for read schemas to handle legacy data gracefully
