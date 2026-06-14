# Personal Companion Agent

This document describes the Personal Companion Agent, a user-owned interface personality.

## Purpose

The Personal Companion Agent is not a knowledge source and is not part of global admin-managed agent orchestration. It is a per-user persona configuration used by the frontend as the user-facing companion identity.

Each user can own at most one companion.

## Core Characteristics

A Personal Companion Agent stores:

- `name`
- `avatar_url`
- `writing_style`
- `active`

Ownership and cardinality:

- Companion is scoped to the authenticated user (`/users/me/...` pattern).
- Exactly one companion per user is enforced at the database layer (`UNIQUE(user_id)`).

## API Endpoints (Frontend)

All endpoints require bearer authentication.

Header:

- `Authorization: Bearer <access_token>`

### 1) Create Companion

- Method: `POST`
- Path: `/users/me/companion`

Request body:

```json
{
  "name": "Echo",
  "writing_style": "Warm, concise, and supportive",
  "active": true
}
```

Success response (`201`):

```json
{
  "id": "9b6d2f61-4c6d-4a4d-96d2-9d7f0f9af8fb",
  "user_id": 12,
  "name": "Echo",
  "avatar_url": null,
  "writing_style": "Warm, concise, and supportive",
  "active": true,
  "created_at": "2026-06-14T18:02:11.123456+00:00",
  "updated_at": "2026-06-14T18:02:11.123456+00:00"
}
```

Conflict response (`409`) when already exists:

```json
{
  "detail": "Personal companion agent already exists for this user"
}
```

### 2) Get Current User Companion

- Method: `GET`
- Path: `/users/me/companion`

Success response (`200`): same shape as create.

Not found response (`404`) when user has not created one yet:

```json
{
  "detail": "Personal companion agent not found"
}
```

### 3) Update Companion

- Method: `PATCH`
- Path: `/users/me/companion`

Request body (partial updates supported):

```json
{
  "writing_style": "Playful but grounded",
  "active": false
}
```

Success response (`200`): updated companion payload.

### 4) Delete Companion

- Method: `DELETE`
- Path: `/users/me/companion`

Success response: `204 No Content`

Not found response (`404`):

```json
{
  "detail": "Personal companion agent not found"
}
```

### 5) Upload Companion Avatar

- Method: `POST`
- Path: `/users/me/companion/avatar`
- Content-Type: `multipart/form-data`
- File field: `file`

Example request with cURL:

```bash
curl -X POST "http://localhost:8100/users/me/companion/avatar" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@/path/to/avatar.png"
```

Success response (`200`): companion payload with updated `avatar_url`.

Validation error (`400`) for unsupported image input:

```json
{
  "detail": "Unsupported image type"
}
```

Not found response (`404`) if companion does not exist yet:

```json
{
  "detail": "Personal companion agent not found"
}
```

## Frontend Integration Pattern

Recommended UI flow:

1. On app load, request `GET /users/me/companion`.
2. If `200`, render companion edit screen with current values.
3. If `404`, render companion creation flow.
4. After successful create/update/avatar upload, update local UI state from returned payload.
5. Handle `409` on create by re-fetching with `GET /users/me/companion`.

## Notes for Product and UX

- Companion identity is user-specific and should not be exposed as a shared/global catalog.
- `writing_style` is intended to drive tone/persona behavior in the user-facing interaction layer.
- Avatar storage is server-managed; frontend sends a file and consumes returned `avatar_url`.

## Implementation References

Primary backend implementation points:

- `shrecknet/app/api/routers/personal_companion_agents.py`
- `shrecknet/app/services/personal_companion_agent_service.py`
- `shrecknet/app/repositories/personal_companion_agent_repository.py`
- `shrecknet/app/models/personal_companion_agent.py`
- `shrecknet/app/schemas/personal_companion_agent.py`
