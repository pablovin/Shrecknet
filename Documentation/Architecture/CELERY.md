# Celery Background Jobs

This document describes the Celery-based background job infrastructure for backend_2, including how to use it, how it works, and how to run the worker processes.

## Overview

The backend_2 application uses Celery for executing long-running background tasks asynchronously. Background jobs are tracked in a separate SQLite database (`backend_2_jobs.db`) to provide visibility into task execution status, progress, and history.

## Architecture

### Components

1. **Celery Application** (`app/celery_app.py`): Main Celery configuration
2. **Job Tracking Model** (`app/models/background_job.py`): SQLAlchemy model for job persistence
3. **Jobs Database** (`backend_2_jobs.db`): Separate SQLite database for job tracking
4. **Task Definitions** (`app/tasks/`): Individual Celery tasks
5. **Job Tracking Utilities** (`app/utils/job_tracking.py`): Helper functions for job lifecycle management
6. **API Endpoints** (`app/api/routers/background_jobs.py`): REST API for job monitoring

### Background Job Information

Each background job tracks the following information:

- **id**: Unique job identifier
- **celery_task_id**: Celery's internal task ID
- **author_type**: Type of author (`user` or `agent`)
- **author_id**: ID of the user or agent that created the job
- **job_type**: Type of background job (e.g., `graph_link_update`, `neo4j_embedding`)
- **status**: Current status (`queued`, `running`, `done`, `failed`)
- **description**: Human-readable description of the job
- **details**: JSON string containing additional job-specific details
- **progress**: Progress value from 0.0 to 1.0 (0% to 100%)
- **error_message**: Error message if the job failed
- **started_at**: Timestamp when the job was created
- **completed_at**: Timestamp when the job completed (success or failure)
- **updated_at**: Timestamp of last update

## Configuration

### Environment Variables

Configure Celery via environment variables:

```bash
# Redis broker and result backend (required for production mode)
export BACKEND_2_CELERY_BROKER_URL="redis://localhost:6379/0"
export BACKEND_2_CELERY_RESULT_BACKEND="redis://localhost:6379/1"

# Optional: Run tasks synchronously in development (no worker needed)
# export BACKEND_2_CELERY_TASK_ALWAYS_EAGER="true"

# Jobs database (defaults to separate SQLite file)
export BACKEND_2_JOBS_DATABASE_URL="sqlite+aiosqlite:///./backend_jobs.db"
```

### Development vs Production

**Production Mode** (`CELERY_TASK_ALWAYS_EAGER=false`):
- Tasks execute asynchronously in worker processes
- Requires Redis (or other broker) and worker processes
- Better performance and isolation
- **Default configuration**
- Recommended for production deployments

**Development Mode** (`CELERY_TASK_ALWAYS_EAGER=true`):
- Tasks execute synchronously in the same process
- No separate worker process needed
- Useful for debugging and testing
- Set `BACKEND_2_CELERY_TASK_ALWAYS_EAGER=true` to enable

## Running Celery Workers

### Prerequisites

1. **Install Redis** (for production mode):
   ```bash
   # macOS
   brew install redis
   brew services start redis

   # Ubuntu/Debian
   sudo apt-get install redis-server
   sudo systemctl start redis

   # Docker
   docker run -d -p 6379:6379 redis:7-alpine
   ```

2. **Configure Environment**:
   ```bash
   export BACKEND_2_CELERY_BROKER_URL="redis://localhost:6379/0"
   export BACKEND_2_CELERY_RESULT_BACKEND="redis://localhost:6379/1"
   export BACKEND_2_CELERY_TASK_ALWAYS_EAGER="false"
   ```

### Starting the Worker

From the `backend` directory:

```bash
# Basic worker
celery -A app.celery_app worker --loglevel=info

# Worker with specific queue
celery -A app.celery_app worker -Q ontology_linking --loglevel=info

# Worker with concurrency control
celery -A app.celery_app worker --concurrency=4 --loglevel=info

# Worker with auto-reload (development)
celery -A app.celery_app worker --loglevel=info --autoreload
```

### Worker Options

- `--loglevel`: Set logging level (debug, info, warning, error, critical)
- `--concurrency`: Number of worker processes (default: number of CPUs)
- `--autoreload`: Automatically reload on code changes (development only)
- `-Q`: Specify queue name (default: `ontology_linking`)
- `--pool`: Worker pool type (prefork, solo, gevent, eventlet)

### Monitoring Workers

```bash
# List active workers
celery -A app.celery_app inspect active

# Check worker stats
celery -A app.celery_app inspect stats

# Check registered tasks
celery -A app.celery_app inspect registered
```

## Current Background Jobs

### 1. Graph Link Update (`graph_link_update`)

Updates cross-reference links between entities in an ontology instance.

**Task Name**: `ontology.link_instance`

**Implementation**: `app/tasks/ontology_links.py`

**Usage**:
```python
from app.tasks.ontology_links import link_instance

# Trigger the task
result = link_instance.delay(
    instance_id="instance-123",
    author_type="user",
    author_id="42"
)
```

**Progress Updates**:
- 10%: Fetching entities
- 30%: Building alias map
- 50%: Linking text
- 80%: Updating database
- 95%: Completed

### 2. Neo4j Embedding (`neo4j_embedding`)

Embeds an ontology instance in Neo4j for semantic search (placeholder).

**Task Name**: `ontology.embed_instance`

**Implementation**: `app/tasks/neo4j_embedding.py`

**Usage**:
```python
from app.tasks.neo4j_embedding import embed_instance

# Trigger the task
result = embed_instance.delay(
    instance_id="instance-123",
    author_type="agent",
    author_id="elder-agent"
)
```

**Note**: This is currently a placeholder task. Full integration with the GraphRAG embedding service is planned for future development.

### 3. PDF Book Embedding (`pdf_book_embedding`)

Embeds a PDF book into Neo4j for semantic search by Librarian agents.

**Task Name**: `library.embed_pdf_book`

**Implementation**: `app/tasks/pdf_embedding.py`

**Usage**:
```python
from app.tasks.pdf_embedding import embed_pdf_book

# Trigger the task
result = embed_pdf_book.delay(
    library_item_id=5,
    ontology_id=1,
    author_type="user",
    author_id="42"
)
```

**Progress Updates**:
- 10%: Fetching library item details
- 20%: Reading PDF file
- 30%: Ensuring vector index
- 40%: Embedding PDF pages
- 90%: Updating library item status
- 100%: Completed

### 4. Architect Analysis (`architect_analysis`)

Analyses ontology instance narrative text to recommend new entity instances or highlight existing ones for review.

**Task Name**: `architect.analyze_instance`

**Implementation**: `app/tasks/architect_analysis.py`

**Usage**:
```python
from app.tasks.architect_analysis import analyze_instance

result = analyze_instance.delay(
    run_id="run-id",
    agent_id="architect-agent-id",
    request_payload={"ontology_instance_id": "instance-123"},
    author_type="user",
    author_id="42",
)
```

**Progress Updates**:
- 5%: Preparing architect analysis
- 15%: Loading ontology instance
- 25%: Chunking story text
- 95%: Completed and storing proposals

**Details**:
- Extracts text from each PDF page
- Creates vector embeddings using sentence-transformers
- Stores chunks in Neo4j as `PdfChunk` nodes
- Updates library item `vectorized` status
- Processes in batches of 20 pages for memory efficiency

## Creating New Background Jobs

### Step 1: Define the Task

Create a new file in `app/tasks/`:

```python
# app/tasks/my_task.py
from __future__ import annotations

import asyncio
from typing import Any

from app.celery_app import celery_app
from app.models.background_job import AuthorType, JobType
from app.utils.job_tracking import (
    create_background_job,
    mark_job_done,
    mark_job_failed,
    mark_job_running,
    update_job_progress,
)


@celery_app.task(name="my.task")
def my_task(
    param: str,
    author_type: str = "agent",
    author_id: str = "system"
) -> dict[str, Any]:
    """My background task."""
    
    # Create job entry
    job_id = asyncio.run(
        create_background_job(
            author_type=AuthorType(author_type),
            author_id=author_id,
            job_type=JobType.MY_TASK_TYPE,  # Add to JobType enum
            description=f"Processing {param}",
            celery_task_id=my_task.request.id,
            details={"param": param},
        )
    )
    
    try:
        # Mark as running
        asyncio.run(mark_job_running(job_id))
        
        # Do work with progress updates
        asyncio.run(update_job_progress(job_id, 0.3, {"status": "step 1"}))
        # ... do work ...
        
        asyncio.run(update_job_progress(job_id, 0.6, {"status": "step 2"}))
        # ... do more work ...
        
        # Mark as complete
        asyncio.run(mark_job_done(job_id, {"result": "success"}))
        return {"job_id": job_id, "status": "success"}
        
    except Exception as e:
        asyncio.run(mark_job_failed(job_id, str(e)))
        raise
```

### Step 2: Add Job Type to Enum

Update `app/models/background_job.py`:

```python
class JobType(str, Enum):
    """Type of background job."""
    GRAPH_LINK_UPDATE = "graph_link_update"
    NEO4J_EMBEDDING = "neo4j_embedding"
    PDF_BOOK_EMBEDDING = "pdf_book_embedding"  # Add your type
```

### Step 3: Register the Task

Update `app/tasks/__init__.py`:

```python
from app.tasks import my_task, neo4j_embedding, ontology_links

__all__ = ["ontology_links", "neo4j_embedding", "my_task"]
```

## API Endpoints

Monitor and manage background jobs via REST API:

### List Jobs

```bash
GET /jobs?job_type=graph_link_update&status=running&limit=50&offset=0
```

**Query Parameters**:
- `author_type`: Filter by author type (`user` or `agent`)
- `author_id`: Filter by author ID
- `job_type`: Filter by job type
- `status`: Filter by status (`queued`, `running`, `done`, `failed`)
- `limit`: Maximum results to return (default: 100, max: 1000)
- `offset`: Pagination offset (default: 0)

### Get Job Details

```bash
GET /jobs/{job_id}
```

### Delete Completed Jobs

```bash
DELETE /jobs
Content-Type: application/json

{
  "job_ids": [1, 2, 3]
}
```

**Note**: Only jobs with status `done` or `failed` can be deleted.

## Best Practices

### 1. Always Track Jobs

Every long-running task should create a background job entry:

```python
job_id = asyncio.run(create_background_job(...))
```

### 2. Update Progress Regularly

Keep users informed with progress updates:

```python
await update_job_progress(job_id, 0.5, {"status": "halfway done"})
```

### 3. Handle Errors Gracefully

Always catch exceptions and mark jobs as failed:

```python
try:
    # ... do work ...
    asyncio.run(mark_job_done(job_id))
except Exception as e:
    asyncio.run(mark_job_failed(job_id, str(e)))
    raise
```

### 4. Use Meaningful Descriptions

Provide clear, human-readable job descriptions:

```python
description=f"Linking entities for ontology instance {instance_id}"
```

### 5. Store Useful Details

Use the `details` field for job-specific information:

```python
details={"instance_id": instance_id, "entity_count": 42}
```

### 6. Use driver.session() for Neo4j Operations

When working with Neo4j in Celery tasks, use `driver.session()` directly instead of async generators:

```python
from app.graph.neo4j import get_driver
from app.core.config import get_settings

async def my_task_impl(job_id: int):
    """Task implementation using driver.session() pattern."""
    driver = get_driver()
    settings = get_settings()
    async with driver.session(database=settings.neo4j_database) as session:
        # Perform Neo4j operations
        result = await session.run("MATCH (n) RETURN count(n)")
        # ...
```

**Why?** When using `run_async()` helper (which handles both eager and async execution modes), 
using `driver.session()` avoids "Future attached to a different loop" errors that can occur 
with async generator patterns like `async for session in get_neo4j_session()`.

**Example:** See `app/tasks/neo4j_embedding.py` and `app/tasks/ontology_links.py` for reference implementations.

## Troubleshooting

### Worker Not Processing Tasks

1. **Check Redis Connection**:
   ```bash
   redis-cli ping  # Should return "PONG"
   ```

2. **Verify Worker is Running**:
   ```bash
   celery -A app.celery_app inspect active
   ```

3. **Check Logs**:
   ```bash
   celery -A app.celery_app worker --loglevel=debug
   ```

### "Future attached to a different loop" Error

This error occurs when async code tries to use futures/tasks from a different event loop.

**When it happens:** This specifically occurs when `celery_task_always_eager=True` (development mode) 
and a Celery task is called from an async context (like a FastAPI endpoint). In this mode, tasks run 
synchronously in the caller's event loop, and the `run_async()` helper must create a separate thread 
with its own event loop to avoid conflicts.

**Symptoms:**
```
Exception: Embedding failed: Task <Task pending name='Task-466'> got Future <Future pending> attached to a different loop
```

**Solution:**
Use `driver.session()` directly instead of async generators when working with Neo4j in tasks:

```python
# ❌ Don't do this
async for session in get_neo4j_session():
    # This creates futures attached to the wrong loop
    result = await session.run(query)

# ✅ Do this instead
driver = get_driver()
settings = get_settings()
async with driver.session(database=settings.neo4j_database) as session:
    # This works correctly with run_async()
    result = await session.run(query)
```

**Why:** The `run_async()` helper creates a new event loop in a separate thread when called 
from an async context (like FastAPI with `celery_task_always_eager=True`). Async generators 
can create tasks attached to the caller's loop, causing conflicts. Using `driver.session()` 
directly creates all async operations in the correct loop.

### Tasks Execute Synchronously

This is expected when `CELERY_TASK_ALWAYS_EAGER=true` (development mode).

To run asynchronously:
```bash
export BACKEND_2_CELERY_TASK_ALWAYS_EAGER="false"
```

### Jobs Not Appearing in Database

1. **Verify Jobs Database Exists**:
   ```bash
   ls -la backend_2_jobs.db
   ```

2. **Check Database Initialization**:
   The jobs database is automatically initialized on app startup.

3. **Verify Job Creation**:
   Check task code includes `create_background_job()` call.

### High Memory Usage

1. **Reduce Worker Concurrency**:
   ```bash
   celery -A app.celery_app worker --concurrency=2
   ```

2. **Use Different Pool**:
   ```bash
   celery -A app.celery_app worker --pool=solo
   ```

## Production Deployment

### Systemd Service Example

Create `/etc/systemd/system/celery-backend2.service`:

```ini
[Unit]
Description=Celery Worker for backend_2
After=network.target redis.service

[Service]
Type=forking
User=shrecknet
Group=shrecknet
WorkingDirectory=/opt/shrecknet/backend
Environment="BACKEND_2_CELERY_BROKER_URL=redis://localhost:6379/0"
Environment="BACKEND_2_CELERY_RESULT_BACKEND=redis://localhost:6379/1"
Environment="BACKEND_2_CELERY_TASK_ALWAYS_EAGER=false"
ExecStart=/opt/shrecknet/venv/bin/celery -A app.celery_app worker \
          --loglevel=info \
          --pidfile=/var/run/celery-backend2.pid \
          --logfile=/var/log/celery-backend2.log
Restart=always

[Install]
WantedBy=multi-user.target
```

### Docker Compose Example

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  backend2-api:
    build: ./backend
    environment:
      - BACKEND_2_CELERY_BROKER_URL=redis://redis:6379/0
      - BACKEND_2_CELERY_RESULT_BACKEND=redis://redis:6379/1
      - BACKEND_2_CELERY_TASK_ALWAYS_EAGER=false
    depends_on:
      - redis

  backend2-worker:
    build: ./backend
    command: celery -A app.celery_app worker --loglevel=info
    environment:
      - BACKEND_2_CELERY_BROKER_URL=redis://redis:6379/0
      - BACKEND_2_CELERY_RESULT_BACKEND=redis://redis:6379/1
      - BACKEND_2_CELERY_TASK_ALWAYS_EAGER=false
    depends_on:
      - redis
```

## Further Reading

- [Celery Documentation](https://docs.celeryproject.org/)
- [Redis Documentation](https://redis.io/documentation)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
