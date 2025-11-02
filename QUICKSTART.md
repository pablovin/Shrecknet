# Quick Start: Backend_2 Docker Deployment

**TL;DR**: Build dependencies once, deploy in seconds! ⚡

## For the Impatient

```bash
# ONE-TIME SETUP (15-30 minutes)
cd backend_2
./build-venv.sh --ml

# EVERY DEPLOY (10-30 seconds!)
cd ..
docker compose build
docker compose up -d

# Access your API
curl http://localhost:8000/docs
```

## What This Does

1. **Builds a .venv folder** with all Python dependencies pre-installed (PyTorch, FastAPI, etc.)
2. **Copies .venv into Docker** instead of installing from scratch
3. **Deploys in seconds** instead of hours

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

## When to Rebuild .venv

❌ Code changes → Just rebuild Docker (10-30 sec)
✅ Dependency changes → Rebuild .venv first (15-30 min)

## Need Help?

- Full guide: [VENV_DEPLOYMENT.md](VENV_DEPLOYMENT.md)
- Workflows: [DEPLOYMENT_WORKFLOW.md](DEPLOYMENT_WORKFLOW.md)
- Summary: [DEPLOYMENT_OPTIMIZATION_SUMMARY.md](DEPLOYMENT_OPTIMIZATION_SUMMARY.md)
- Docker info: [DOCKER.md](DOCKER.md)
