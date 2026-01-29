# Quick Start: Backend_2 Docker Deployment

**TL;DR**: Clone, run `docker compose up --build`, done. ⚡

## For the Impatient

```bash
# BUILD + RUN
docker compose up --build

# Access your API
curl http://localhost:8000/docs
```

## What This Does

1. **Builds Docker images** for the API, worker, and dependencies
2. **Starts services** (backend, worker, Neo4j, Redis)
3. **Exposes the API** on http://localhost:8000

## Services Running

After `docker compose up -d`:
- **backend_2** - FastAPI app at http://localhost:8000
- **backend_2_worker** - Celery worker with ML capabilities
- **neo4j** - Graph database at http://localhost:7474
- **redis** - Message broker at localhost:6379

## Common Commands

```bash
# View logs
docker compose logs -f backend_2

# Restart a service
docker compose restart backend_2

# Stop everything
docker compose down

# Stop and delete all data (⚠️ WARNING)
docker compose down -v
```

## Need Help?

- Workflows: [DEPLOYMENT_WORKFLOW.md](DEPLOYMENT_WORKFLOW.md)
- Docker info: [DOCKER.md](DOCKER.md)
