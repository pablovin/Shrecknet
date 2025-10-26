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

- Register users with `POST /users/`; passwords are hashed using bcrypt before persistence.
- The first account created automatically becomes an `admin`, regardless of the requested role,
  so newly deployed environments always begin with a privileged operator.
- Exchange credentials for a JWT access token via `POST /auth/token` and include it as
  `Authorization: Bearer <token>` on protected requests.
- Only `admin` and `world_builder` roles may access ontology CRUD routes.
- Users can update their own profile data; only admins may change user roles.

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
- Ontologies: hierarchical bundle of entities, properties, and relationships with provenance flags
  and media metadata.

## Testing

```bash
pytest
```

The tests spin up an isolated in-memory SQLite database and exercise authentication plus ontology
CRUD operations end-to-end through the HTTP layer.
