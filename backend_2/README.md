# backend_2

A modular FastAPI service that exposes REST endpoints to manage ontologies, their entities, properties, and relationships. The service uses an asynchronous SQLAlchemy stack with SQLite by default, but can be pointed at any SQLAlchemy-supported database by changing the `BACKEND_2_DATABASE_URL` environment variable.

## Architecture

- **API layer** (`app/api`) – FastAPI routers with clean request/response schemas.
- **Service layer** (`app/services`) – Business logic, validation, and transaction boundaries.
- **Repository layer** (`app/repositories`) – Data access objects wrapping SQLAlchemy queries.
- **Database layer** (`app/db`) – Session management and declarative models.
- **Security** (`app/core/security.py`) – Password hashing, JWT handling, and reusable role checks.

This separation keeps concerns isolated so that swapping persistence technologies or extending business rules is straightforward.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[test]
uvicorn app.main:app --reload
```

By default the app stores data in `backend_2.db`. Override via:

```bash
export BACKEND_2_DATABASE_URL="postgresql+asyncpg://user:pass@host/db"
```

FastAPI automatically generates interactive docs at `/docs` and `/redoc`.

## Authentication & authorization

- Register users with `POST /users/`; passwords are hashed using Argon2 for strong, modern
  protection.
- Passwords accept any characters, require at least six, and have no maximum length constraints.
- The first account created automatically becomes an `admin`, regardless of the requested role,
  so newly deployed environments always begin with a privileged operator.
- Exchange credentials for a JWT access token via `POST /auth/token` and include it as
  `Authorization: Bearer <token>` on protected requests.
- Only `admin` and `world_builder` roles may access ontology CRUD routes.
- Users can update their own profile data; only admins may change user roles.
- Admins (or the user themselves) can remove accounts via `DELETE /users/{user_id}`; deletions are
  audited like other mutations.
- Pre-registration UI can call `GET /users/availability?username=...&email=...` to check whether
  credentials are free before attempting signup.
- All API errors are logged with method, path, status, and detail to help operators diagnose issues
  quickly; general request timing is also recorded for non-successful responses.
- Every create/update/delete on users or ontologies is persisted to the `audit_logs` table. Admins
  can review history via `GET /logs/`, filtering by actor type (user/agent), actor id, entity type,
  entity id, action, and date window.

## Media uploads

- Configure where images live with `BACKEND_2_MEDIA_ROOT` (default `./media`) and how they are
  exposed with `BACKEND_2_MEDIA_BASE_URL` (default `/media`). The directory is mounted automatically
  by FastAPI.
- Use `BACKEND_2_MEDIA_PUBLIC_URL` when the API sits behind a proxy or another domain (for example
  set it to `https://shrecknet.club/media`) so generated links are fully-qualified for the frontend.
- Files are validated (<10 MB by default), resized to fit within the configured max dimensions, and
  optimized before being written to disk.
- Users can update their avatar with `POST /users/{user_id}/avatar` (multipart form upload). The
  endpoint stores the avatar as a deterministic `user_{username}.png` and returns the final URL for
  frontend use.
- Admins or world builders can upload other assets via `POST /media-admin/images`, providing the target
  model name (e.g., `user`, `ontology`, `notification`, `ontology_instance`) and the corresponding
  entity id. Images are resized using the same limits as avatars and written deterministically to
  `/media/{model}/{id}/image_url.png`, overwriting previous uploads for that entity.

## Notifications

- Admins and world builders can curate player-facing updates via `/notifications/`. CRUD routes let
  operators target a specific user, choose a type (`content_update`, `new_features`,
  `session_updates`, or `note_updates`), supply rich-text descriptions, and decide whether the entry
  starts marked as read.
- Each notification tracks author attribution (`user` or `agent`), the delivery timestamp, whether
  it should also email the recipient, and the read state; when an email is dispatched the
  `sent_date` is recorded for auditability.
- Authenticated users access their feed with `GET /notifications/me`, toggle the read flag via
  `POST /notifications/{id}/read`, and retrieve unread totals using
  `GET /notifications/me/unread-count`.

## Ontology library

- Manage knowledge artifacts per ontology under `/libraries/{ontology_id}/items`. Admins/world
  builders upload PDFs (up to 300 MB) via multipart form data; the file is stored deterministically
  as `/library/{ontology_id}/{item_id}/content.pdf` underneath the media root, replacing prior
  uploads in place.
- Each item exposes metadata (title, description, optional cover URL) plus vectorisation markers
  (`vectorized`, `last_vectorized_at`) for downstream processing; consumers fetch the file via the
  returned `pdf_url`.
- Authenticated users can browse library entries, flag notable pages with bookmarks, and share
  bookmarks with selected collaborators (`POST /libraries/items/{item_id}/bookmarks`,
  `PUT /libraries/bookmarks/{bookmark_id}`, `DELETE /libraries/bookmarks/{bookmark_id}`).
- Bookmarks capture page, title, optional description, privacy flag, and explicit share lists so
  personal notes stay private while team highlights are broadcast.

## Notes

- `/notes` lets users capture rich-text snippets with optional ontology context. Owners can edit,
  delete, and (re)share their notes; collaborators view shared notes via `/notes/shared`.
- Sharing accepts an explicit user list and fires `note_updates` notifications so recipients are
  alerted immediately.
- Updates allow changing the share roster (including clearing it) and re-issuing notifications only
  to newly added collaborators.

## Games & sessions

- Tabletop campaigns live under `/games`. Admins and world builders create games tied to an ontology
  and manage their membership; players automatically see the games they belong to via
  `GET /games/mine`.
- Each game owns multiple sessions with metadata (title, description, location, scheduled date) and
  attendance tracking (`POST /games/{game_id}/sessions/{session_id}/attendance`).
- Scheduling polls let admins propose candidate times, members vote, and the admin selects the final
  option—automatically promoting voters of the chosen slot to the attendee list and fixing the
  session date.
- Session creation, poll creation, and poll finalisation send notifications to every game member so
  they stay in sync with upcoming events.

## Ontology instances & Neo4j

- Ontology definitions remain in the SQL database; ontology *instances* (runtime graphs) are stored
  in Neo4j, allowing the structure to evolve without SQL migrations.
- A boolean `display_on_world` flag (default `true`) on each ontology controls whether its items
  surface on the world page; toggle via the `/ontologies/{id}` update endpoint.
- Configure Neo4j via `BACKEND_2_NEO4J_URI`, `BACKEND_2_NEO4J_USER`, `BACKEND_2_NEO4J_PASSWORD`,
  and `BACKEND_2_NEO4J_DATABASE` (defaults expect `neo4j/neo4j` running on `bolt://localhost:7687`).
- Quick start Neo4j locally:

  ```bash
  docker run --rm \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/VeryStrongPass123 \
  -e NEO4J_PLUGINS='["apoc", "graph-data-science"]' \
  --name shrecknet-neo4j neo4j:5-community

  ```

  Update the password on first login (and mirror it in the backend env vars).
- CRUD endpoints under `/ontology-instances` let admins/world builders materialise ontology
  entities, populate property values, and wire relationships (respecting the ontology
  definitions). Each entity instance captures canonical fields (`text`, `node_avatar_url`,
  `autogenerated_text`, `created_date`, `last_updated_date`, `author_type`, `author_id`) alongside
  optional ontology-defined properties/relationships. Search supports name/description filters and
  pagination.

### CORS configuration

Cross-origin access is controlled via environment variables exposed by `Settings`:

- `BACKEND_2_CORS_ALLOW_ORIGINS` – comma-separated list of allowed origins (default includes
  localhost and `https://lovableproject.com`).
- `BACKEND_2_CORS_ALLOW_ORIGIN_REGEX` – optional regex matched against request origins (defaults to
  allowing any subdomain of `lovableproject.com`).
- `BACKEND_2_CORS_ALLOW_METHODS`, `BACKEND_2_CORS_ALLOW_HEADERS`, `BACKEND_2_CORS_ALLOW_CREDENTIALS`
  – tune allowed verbs, headers (pre-populated with `GET/POST/PUT/DELETE/OPTIONS` and
  `Authorization, Content-Type`), and credential support.

## Data model overview

- Users: username, hashed password, full name, email, timezone, role, optional avatar URL, optional
  linked ontology entity ids.
- Library items: ontology-linked PDFs with title/description, optional cover imagery, deterministic
  storage paths, vectorisation status metadata, and user-created bookmarks (private or shared).
- Notes: rich-text documents owned by a user, optionally linked to an ontology, with many-to-many
  sharing semantics and notification hooks when collaborators are added.
- Games: campaign containers referencing an ontology, a member roster, associated sessions, polls,
  and attendance flags for each scheduled session.
- Ontologies: contain entities; each entity owns its properties and relationships (relationships
  reference a source entity and an optional destination entity within the same ontology) alongside
  provenance metadata and optional media references.
- Notifications: per-user records with typed categories, author attribution, delivery timestamps,
  titles, rich descriptions, read flags, and email-delivery metadata (`send_email`, `sent_date`) to
  power inbox and messaging workflows.

## Testing

```bash
pytest
```

The tests spin up an isolated in-memory SQLite database and exercise authentication plus ontology
CRUD operations end-to-end through the HTTP layer.
