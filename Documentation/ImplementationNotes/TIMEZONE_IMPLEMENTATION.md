# Timezone Support Implementation

## Overview

This document describes the timezone support implementation for game sessions, polls, and tables in the backend_2 application.

## Problem Statement

Sessions, polls, and tables now require timezone information in all datetime fields to prevent ambiguity and ensure consistent time handling across the platform.

## Solution

### 1. Model Configuration

All datetime fields in the game-related models use SQLAlchemy's `DateTime(timezone=True)`:

```python
from sqlalchemy import DateTime

class Game(Base):
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

#### Affected Models and Fields

- **Game**
  - `created_at`: Auto-generated on creation
  - `updated_at`: Auto-updated on modification

- **GameSession**
  - `scheduled_date`: User-provided (nullable)
  - `created_at`: Auto-generated on creation
  - `updated_at`: Auto-updated on modification

- **GameSessionAttendance**
  - `responded_at`: Auto-generated when user responds

- **GameSessionPoll**
  - `created_at`: Auto-generated on poll creation

- **GameSessionPollOption**
  - `proposed_start`: User-provided datetime for poll option
  - `created_at`: Auto-generated on option creation

- **GameSessionPollVote**
  - `created_at`: Auto-generated on vote submission

### 2. SQLAlchemy Behavior

When using `DateTime(timezone=True)`:

- **For auto-generated fields** (using `func.now()`):
  - SQLAlchemy generates timezone-aware datetimes in UTC
  - Stored with timezone offset (e.g., `2024-01-15 10:30:00+00:00`)

- **For user-provided fields**:
  - Accepts both timezone-aware and naive datetime objects
  - Naive datetimes are treated as UTC and stored with `+00:00`
  - Timezone-aware datetimes are converted to UTC for storage
  - All returned values have `tzinfo` set (timezone-aware)

### 3. Migration for Existing Data

The migration function `migrate_game_datetimes_to_brussels_timezone()` ensures all existing data has timezone information.

#### Migration Behavior

- Scans all game-related tables for datetime fields
- Identifies naive datetime strings (those without `+` or `Z`)
- Appends Brussels timezone offset (`+01:00`) to naive datetimes
- Idempotent: can be safely run multiple times

#### Tables and Columns Migrated

```python
table_columns = {
    "games": ["created_at", "updated_at"],
    "game_sessions": ["scheduled_date", "created_at", "updated_at"],
    "game_session_polls": ["created_at"],
    "game_session_poll_options": ["proposed_start", "created_at"],
    "game_session_poll_votes": ["created_at"],
    "game_session_attendance": ["responded_at"],
}
```

#### Running the Migration

The migration runs automatically during database initialization via `init_db()`:

```python
async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # ... other migrations ...
    
    # Run game datetime timezone migration
    await migrate_game_datetimes_to_brussels_timezone(engine)
```

### 4. API Usage

#### Creating Sessions with Timezone-Aware Datetimes

When creating or updating sessions via the API, clients should provide timezone-aware datetimes:

**Good - ISO 8601 format with timezone:**
```json
{
  "title": "Session Zero",
  "scheduled_date": "2024-12-25T18:00:00+01:00",
  "location": "Online"
}
```

**Also accepted - UTC timezone:**
```json
{
  "title": "Session Zero",
  "scheduled_date": "2024-12-25T18:00:00Z",
  "location": "Online"
}
```

**Also works - Naive datetime (treated as UTC):**
```json
{
  "title": "Session Zero",
  "scheduled_date": "2024-12-25T18:00:00",
  "location": "Online"
}
```

Note: While naive datetimes are accepted, they're treated as UTC. Clients should provide explicit timezone information.

#### Creating Poll Options

```json
{
  "options": [
    {"proposed_start": "2024-12-26T19:00:00+01:00"},
    {"proposed_start": "2024-12-27T20:00:00+01:00"}
  ]
}
```

### 5. Database Storage

For SQLite (development and testing):
- Datetimes stored as ISO 8601 strings with timezone offset
- Example: `2024-01-15 10:30:00+01:00`

For PostgreSQL (production, if used):
- Datetimes stored as `TIMESTAMP WITH TIME ZONE`
- Automatically handles timezone conversions

## Guarantees

With this implementation:

1. ✅ **All new records** created via SQLAlchemy have timezone information
2. ✅ **All existing records** are migrated to have Brussels timezone
3. ✅ **All datetime fields** in game models use `DateTime(timezone=True)`
4. ✅ **Auto-generated timestamps** (created_at, updated_at, responded_at) have timezone info
5. ✅ **User-provided datetimes** are stored with timezone information (UTC if naive)

## Testing

### Migration Tests

Tests in `tests/test_migrations.py`:

- `test_migrate_game_datetimes_to_brussels_timezone`: Verifies migration adds timezone
- `test_migrate_game_datetimes_idempotent`: Verifies safe re-running
- `test_migrate_game_datetimes_skips_when_no_tables`: Verifies graceful handling

### Running Tests

```bash
cd backend_2
pytest tests/test_migrations.py::test_migrate_game_datetimes_to_brussels_timezone -v
```

## Notes

- **Brussels Timezone**: The migration uses `+01:00` as the Brussels timezone offset. This is the standard time offset (CET). During daylight saving time, Brussels uses `+02:00` (CEST), but for simplicity, the migration uses `+01:00` for all existing records.

- **Future Considerations**: If precise DST handling is needed, the migration could be enhanced to calculate the correct offset based on the datetime value and Brussels timezone rules.

- **SQLAlchemy Defaults**: When using `func.now()`, SQLAlchemy generates timestamps in UTC with `+00:00` offset, which is correct and unambiguous.
