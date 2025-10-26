# backend_2

A modular FastAPI service that exposes REST endpoints to manage ontologies, their entities, properties, and relationships. The service uses an asynchronous SQLAlchemy stack with SQLite by default, but can be pointed at any SQLAlchemy-supported database by changing the `BACKEND_2_DATABASE_URL` environment variable.

## Architecture

- **API layer** (`app/api`) – FastAPI routers with clean request/response schemas.
- **Service layer** (`app/services`) – Business logic, validation, and transaction boundaries.
- **Repository layer** (`app/repositories`) – Data access objects wrapping SQLAlchemy queries.
- **Database layer** (`app/db`) – Session management and declarative models.

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

## Testing

```bash
pytest
```

The tests spin up an isolated in-memory SQLite database to exercise the happy-path CRUD flows end-to-end through the HTTP layer.
