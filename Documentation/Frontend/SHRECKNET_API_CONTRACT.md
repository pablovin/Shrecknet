# Shrecknet Frontend API Contract

This document is the frontend-facing contract for the standalone `shrecknet` backend in this repository.

Source of truth:
- App entrypoint: [shrecknet/app/main.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/main.py)
- Routers: [shrecknet/app/api/routers](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/api/routers)

## Base assumptions

- Base URL: whatever host the `shrecknet` service is deployed on.
- Auth: bearer JWT in `Authorization: Bearer <token>`.
- Docs/OpenAPI: FastAPI defaults are enabled, so `/openapi.json`, `/docs`, and `/redoc` should exist unless deployment disables them externally.
- Most endpoints require authentication.

## Auth contract

`shrecknet` auth is not the same as the monolith auth contract.

### `POST /auth/token`

Preferred legacy-compatible request body is form data:

```text
username=testuser
password=secret
```

Accepted identifier semantics:
- Send `username` as either the actual username or the user's email.
- `shrecknet` also tolerates JSON payloads for compatibility:

```json
{
  "username": "testuser",
  "password": "secret"
}
```

Response:

```json
{
  "access_token": "jwt",
  "token_type": "bearer"
}
```

### `GET /auth/jwks`

Response:

```json
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "..."
    }
  ]
}
```

Frontend impact:
- The old backend used `OAuth2PasswordRequestForm`, so login was form-encoded and accepted `username`.
- `shrecknet` should keep that same pipeline.
- Backend resolution is `username` first, then `email`, so existing username-based login still works and email login also works.
- `shrecknet` adds a JWKS endpoint for token verification.

## Core route groups

### Shared core APIs kept in `shrecknet`

- `GET /health`
- `/users`
- `/ontologies`
- `/ontology-instances`
- `/agents`
- `/jobs`
- `/jobs/architect`
- `/jobs/elder`
- `/jobs/elder/chats`
- `/jobs/librarian`
- `/jobs/novelist`
- `/graphrag`
- `/config`
- `/backups`
- `/llm_status`
- `/setup`

### Shrecknet-specific APIs

- `/worlds`
- `/contracts`
- `/events`
- `/media`
- `/media-admin`
- `/libraries`

### APIs removed from `shrecknet` and still belonging to the old app / RPG side

- `/games`
- `/notes`
- `/notifications`
- `/page-visits`
- `/imports`
- `/legacy`
- `/admin-notes`

## Endpoint details the frontend should care about

### Worlds

#### `GET /worlds`

Response:

```json
[
  {
    "id": "world-id",
    "name": "World Name",
    "ontology_ids": ["ontology-a", "ontology-b"]
  }
]
```

#### `GET /worlds/{world_id}`

Same shape as list item.

### Cross-service contracts

These are meant for consumers that should not read the `shrecknet` DB directly.

#### `GET /contracts/users/me`
#### `GET /contracts/users/{user_id}`

Response:

```json
{
  "id": "1",
  "role": "admin",
  "full_name": "Admin User",
  "email": "admin@example.com"
}
```

#### `GET /contracts/worlds/{world_id}`

Response:

```json
{
  "id": "world-id",
  "name": "World Name",
  "ontology_ids": ["ontology-a", "ontology-b"]
}
```

### Events

#### `POST /events/emit`

Request:

```json
{
  "event_type": "something.happened",
  "payload": {
    "key": "value"
  }
}
```

Response:

```json
{
  "status": "published",
  "event_id": "uuid"
}
```

### Media split

#### `GET /media`

Read endpoint. Response:

```json
[
  {
    "id": "media-id",
    "scope": "user-avatar",
    "url": "/media/..."
  }
]
```

#### `POST /media-admin/images`
#### `POST /media-admin/pdfs`

Upload/admin endpoints live under `/media-admin`, not `/media`.

Frontend impact:
- In the monolith, the upload endpoints were mounted directly on `/media-admin` with tag `media`.
- In `shrecknet`, listing and uploading are split into separate routers.

### Libraries

Canonical routes:
- `GET /libraries/{ontology_id}/items`
- `POST /libraries/{ontology_id}/items`
- `GET /libraries/{ontology_id}/items/{item_id}`
- `PUT /libraries/{ontology_id}/items/{item_id}`
- `DELETE /libraries/{ontology_id}/items/{item_id}`
- `POST /libraries/{ontology_id}/items/{item_id}/content`
- `POST /libraries/{ontology_id}/items/{item_id}/trigger-embedding`
- `GET /libraries/{ontology_id}/items/{item_id}/embedding-status`
- `GET /libraries/items/{item_id}/bookmarks`
- `POST /libraries/items/{item_id}/bookmarks`
- `PUT /libraries/bookmarks/{bookmark_id}`
- `DELETE /libraries/bookmarks/{bookmark_id}`
- `DELETE /libraries/bookmarks/{bookmark_id}/share/me`

Frontend impact versus monolith:
- `ontology_id` is now string-shaped in `shrecknet`, not integer-shaped.
- `shrecknet` exposes monolith compatibility routes for the current frontend client:
  `GET /libraries/embedding-stats?ontology_id=...`,
  `GET /libraries/embedding-jobs?ontology_id=...`,
  `DELETE /libraries/{ontology_id}/items/{item_id}/embeddings`,
  `DELETE /libraries/admin/clear-all-embeddings`,
  and `PUT /libraries/{ontology_id}/items/{item_id}/pdf`.
- Response models are still broadly similar for CRUD, but do not assume integer ontology IDs.

### Ontology instances

Canonical routes:
- `GET /ontology-instances/search`
- `POST /ontology-instances/`
- `GET /ontology-instances/`
- `GET /ontology-instances/count`
- `GET /ontology-instances/basic`
- `GET /ontology-instances/by-alias/{slug_alias}`
- `GET /ontology-instances/favorites`
- `POST /ontology-instances/favorites`
- `DELETE /ontology-instances/favorites/{instance_id}`
- `GET /ontology-instances/{instance_id}`
- `GET /ontology-instances/{instance_id}/full`
- `PUT /ontology-instances/{instance_id}`
- `DELETE /ontology-instances/{instance_id}`
- `GET /ontology-instances/{instance_id}/favorites/users`
- `GET /ontology-instances/{instance_id}/events`
- `POST /ontology-instances/{instance_id}/events`
- `GET /ontology-instances/{instance_id}/events/{event_id}`
- `PUT /ontology-instances/{instance_id}/events/{event_id}`
- `DELETE /ontology-instances/{instance_id}/events/{event_id}`

Frontend impact versus monolith:
- `ontology_id` is string-shaped in requests and responses.
- Favorite routes changed:
  - Old: `POST /ontology-instances/{instance_id}/favorite`
  - Old: `DELETE /ontology-instances/{instance_id}/favorite`
  - Old: `GET /ontology-instances/{instance_id}/is-favorite`
  - New: `POST /ontology-instances/favorites`
  - New: `DELETE /ontology-instances/favorites/{instance_id}`
- `GET /ontology-instances/{instance_id}/favorites/users` now returns plain objects with `id`, `email`, `full_name`, `role`, not the old `UserRead` schema.
- Search/count/basic query parameters still exist, but some integer-only filters from the monolith are effectively loosened or ignored in `shrecknet`:
  - `ontology_id` is string-based.
  - `entity_definition_id` is accepted on some endpoints but not meaningfully used in current `shrecknet` implementation.

## High-value migration differences from `backend/app`

### Authentication

- Old backend login was form-data.
- `shrecknet` should remain compatible with form-data login.
- `username` may contain either a username or an email address.
- `shrecknet` explicitly takes `email`.

### Route ownership

Move frontend calls for core graph/world/agent features to `shrecknet`.

Keep frontend calls for RPG/product features out of `shrecknet`:
- games
- notes
- notifications
- page visits

### ID types

The biggest contract shift is integer-heavy monolith routes becoming string-ID routes in `shrecknet`, especially:
- `world_id`
- `ontology_id`
- many contract DTOs

Frontend should not coerce these IDs to numbers.

## Verification status

Verified from code:
- Router inventory in [shrecknet/app/main.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/main.py)
- Auth contract in [shrecknet/app/api/routers/auth.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/api/routers/auth.py)
- Worlds contract in [shrecknet/app/api/routers/worlds.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/api/routers/worlds.py)
- Contracts contract in [shrecknet/app/api/routers/contracts.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/api/routers/contracts.py)
- Events contract in [shrecknet/app/api/routers/events.py](/home/pablovin/workplace/Shrecknet_Github/Shrecknet/shrecknet/app/api/routers/events.py)
- Media/library/ontology-instance differences against `backend/app` via direct file diffs

Recommended frontend rule:
- Treat this document plus `/openapi.json` from the running `shrecknet` service as the current contract pair.
