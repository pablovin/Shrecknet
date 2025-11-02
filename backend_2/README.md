# backend_2

A modular FastAPI service that exposes REST endpoints to manage ontologies, their entities, properties, and relationships. The service uses an asynchronous SQLAlchemy stack with SQLite by default, but can be pointed at any SQLAlchemy-supported database by changing the `BACKEND_2_DATABASE_URL` environment variable.

## Architecture

- **API layer** (`app/api`) – FastAPI routers with clean request/response schemas.
- **Service layer** (`app/services`) – Business logic, validation, and transaction boundaries.
- **Repository layer** (`app/repositories`) – Data access objects wrapping SQLAlchemy queries.
- **Database layer** (`app/db`) – Session management and declarative models.
- **Security** (`app/core/security.py`) – Password hashing, JWT handling, and reusable role checks.
- **GraphRAG** (`app/graphrag`) – Semantic retrieval over Neo4j with multilingual embeddings.

This separation keeps concerns isolated so that swapping persistence technologies or extending business rules is straightforward.

## Getting started

### Local Development

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

### Docker Deployment (Recommended for Production)

For **lightning-fast Docker builds** (~10-30 seconds), use the .venv pre-build approach:

```bash
# One-time: Build .venv with all dependencies (15-30 minutes)
./build-venv.sh --ml

# Every deploy: Super fast builds (10-30 seconds)
cd ..
docker compose build
docker compose up -d
```

See [../VENV_DEPLOYMENT.md](../VENV_DEPLOYMENT.md) for complete deployment guide.

**Without .venv** (traditional build, takes 15-30 minutes):
```bash
cd ..
docker compose build
docker compose up -d
```

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


### Admin Media Upload Endpoint

Admins and world builders can upload images via `POST /media-admin/images` with a flexible content-based organization system.

**Endpoint:** `POST /media-admin/images`

**Authentication:** Requires `ADMIN` or `WORLD_BUILDER` role

**Request Parameters (multipart/form-data):**
- `file` (required): The image file to upload (PNG, JPEG, GIF, BMP, WebP)
- `content_type` (required): String identifying the content type (e.g., `user`, `avatar`, `post`, `gallery`)
- `content_id` (required): String identifying the specific content instance (e.g., user ID, post ID)
- `is_main` (optional, default: `false`): Boolean indicating if this is the main image for this content

**Folder Structure:**
Images are organized as: `media/{content_type}/{content_id}/`

**File Naming:**
- **Main images** (`is_main=true`): Saved as `file.png` and overwrites any existing main file
- **Non-main images** (`is_main=false`): Saved with incremental IDs (`1.png`, `2.png`, `3.png`, etc.)

**Example: Upload a user avatar (main image)**
```bash
curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@avatar.png" \
  -F "content_type=user" \
  -F "content_id=123" \
  -F "is_main=true"
```
**Response:**
```json
{
  "url": "/media/user/123/file.png"
}
```

**Example: Upload gallery images (non-main)**
```bash
# First image
curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@photo1.jpg" \
  -F "content_type=gallery" \
  -F "content_id=456" \
  -F "is_main=false"
```
**Response:**
```json
{
  "url": "/media/gallery/456/1.png"
}
```

```bash
# Second image
curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@photo2.jpg" \
  -F "content_type=gallery" \
  -F "content_id=456" \
  -F "is_main=false"
```
**Response:**
```json
{
  "url": "/media/gallery/456/2.png"
}
```

**Example: Upload a post with main image and additional images**
```bash
# Main image
curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@post-cover.png" \
  -F "content_type=post" \
  -F "content_id=789" \
  -F "is_main=true"
# Response: {"url": "/media/post/789/file.png"}

# Additional images
curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@screenshot1.png" \
  -F "content_type=post" \
  -F "content_id=789" \
  -F "is_main=false"
# Response: {"url": "/media/post/789/1.png"}

curl -X POST "http://localhost:8000/media-admin/images" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@screenshot2.png" \
  -F "content_type=post" \
  -F "content_id=789" \
  -F "is_main=false"
# Response: {"url": "/media/post/789/2.png"}
```

**Notes:**
- All images are automatically converted to PNG format and optimized
- Images are resized to fit within the configured max dimensions (default 1024x1024)
- The `content_type` is converted to lowercase and used as a folder name
- File size limit is 10 MB by default (configurable via `BACKEND_2_MAX_IMAGE_UPLOAD_BYTES`)
- Main images always overwrite the previous main image for that content
- Non-main images use incremental numbering starting from 1
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
- Each ontology entity exposes a `display_on_world` flag (default `true`) to control which entries
  surface on the world page; toggle via
  `/ontologies/{ontology_id}/entities/{entity_id}`.
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
- Whenever an instance changes, a Celery task recalculates wiki-style links inside `text` and
  `autogenerated_text`, producing `text_linked` / `autogenerated_text_linked` fields so cross
  references stay in sync without impacting request latency.

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

## GraphRAG - Semantic Retrieval

The GraphRAG module provides semantic search over Neo4j knowledge graphs using multilingual embeddings. See [GRAPHRAG.md](GRAPHRAG.md) for full documentation.

**Quick start:**
```bash
# Ensure vector index exists
POST /graphrag/index/ensure

# Embed an ontology
POST /graphrag/embed/ontology
{
  "ontology_id": 1,
  "batch_size": 50
}

# Semantic search
POST /graphrag/search
{
  "query": "Who is the Prince of Chicago?",
  "ontology_id": 1,
  "k": 5
}
```

**Features:**
- Multilingual embeddings (384-dim, 50+ languages)
- Per-ontology compartmentalization
- Real-time performance for chat interfaces
- KNN search with neighborhood expansion
- LLM-ready context formatting

## Testing

```bash
pytest
```

The tests spin up an isolated in-memory SQLite database and exercise authentication plus ontology
CRUD operations end-to-end through the HTTP layer.
- Background work uses Celery (broker/result URLs via `BACKEND_2_CELERY_BROKER_URL` and
  `BACKEND_2_CELERY_RESULT_BACKEND`). The default configuration runs tasks eagerly so development
  doesn’t require a broker; disable eagerness in production to offload processing to workers.

## Background Jobs

All background jobs are tracked in a separate SQLite database (`backend_2_jobs.db`) for monitoring
and management. Each job includes:
- Author information (user or agent ID)
- Job type (graph link updates, Neo4j embeddings, etc.)
- Status tracking (queued, running, done, failed)
- Progress indication (0-100%)
- Detailed execution history

**Current background jobs:**
- **Graph Link Updates** (`graph_link_update`): Automatically creates cross-reference links between
  entities in an ontology instance
- **Neo4j Embeddings** (`neo4j_embedding`): Embeds ontology instances for semantic search
  (placeholder)

**API Endpoints:**
- `GET /jobs` - List all background jobs with filtering options
- `GET /jobs/{job_id}` - Get specific job details
- `DELETE /jobs` - Delete completed or failed jobs

For detailed information on running Celery workers, creating new background jobs, and monitoring
task execution, see [CELERY.md](CELERY.md).

## Backup and Restore

The backup system provides comprehensive data export and import capabilities for disaster recovery
and data migration. See [BACKUP_API.md](BACKUP_API.md) for complete documentation.

**Quick start:**
```bash
# Create a backup (admin only)
curl -X POST "http://localhost:8000/backups/create" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"

# List available backups
curl -X GET "http://localhost:8000/backups/" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"

# Download a backup
curl -X GET "http://localhost:8000/backups/backup_20231202_153045.tar.gz/download" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -o backup.tar.gz

# Restore from backup (destructive - deletes all existing data!)
curl -X POST "http://localhost:8000/backups/restore" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file=@backup.tar.gz"
```

**What's backed up:**
- All database tables (users, games, ontologies, agents, library, notes, etc.)
- All Neo4j graph data (nodes and relationships)
- All media files (avatars, library PDFs, etc.)

**Backup storage:**
- Backups are stored in `/media/backups/` as timestamped `.tar.gz` archives
- Backups can be downloaded for off-site storage
- Automated backup scripts are available in `examples/backup_example.py`

**API Endpoints:**
- `POST /backups/create` - Create a new backup
- `GET /backups/` - List all available backups
- `GET /backups/{filename}/download` - Download a backup file
- `POST /backups/restore` - Restore from an uploaded backup file
