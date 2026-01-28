# Docker Deployment Guide for Backend_2

This guide explains how to run the backend_2 application using Docker Compose.

> **⚡ NEW: Lightning-Fast Builds!** For ~10-30 second build times, see [VENV_DEPLOYMENT.md](VENV_DEPLOYMENT.md)

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2.0+
- Minimum 3 CPU cores
- 16GB RAM
- 20GB disk space

## Architecture

The docker-compose setup includes:

1. **backend_2** - Main FastAPI application (port 8000)
2. **backend_2_worker** - Celery worker for async tasks
3. **redis** - Message broker for Celery (port 6379)
4. **neo4j** - Graph database (ports 7474, 7687)

## Quick Start

### Option 1: Lightning-Fast Build (Recommended)

For **10-30 second builds**, pre-build your dependencies first:

```bash
# Build .venv once (takes 15-30 minutes)
cd backend_2
./build-venv.sh --ml

# Now Docker builds are super fast!
cd ..
docker compose build    # ~10-30 seconds ⚡
docker compose up -d
```

See [VENV_DEPLOYMENT.md](VENV_DEPLOYMENT.md) for complete details.

### Option 2: Standard Build

Build dependencies from scratch (takes 15-30 minutes):

```bash
docker compose build    # ~15-30 minutes
docker compose up -d
```

### Validate Configuration

Before starting, validate the Docker setup:

```bash
./validate-docker.sh
```

This script checks:
- Docker and Docker Compose installation
- docker-compose.yml syntax
- Required services are defined
- Persistent volumes are configured

### Start Services

```bash
# Build and start all services
docker compose up -d

# View logs
docker compose logs -f

# Stop all services
docker compose down

# Stop and remove all data (WARNING: This deletes all persistent data)
docker compose down -v
```

## Persistent Data

All data is stored in Docker volumes and persists across container restarts:

- **backend-media**: Media files uploaded to the application
- **backend-data**: SQLite databases (backend_2.db, backend_2_jobs.db)
- **neo4j-data**: Neo4j graph database
- **redis-data**: Redis persistence

These volumes are NOT deleted when you run `docker-compose down`. To remove them, use:
```bash
docker-compose down -v
```

## Accessing Services

- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Media Files**: http://localhost:8000/media/
- **Neo4j Browser**: http://localhost:7474 (user: neo4j, password: VeryStrongPass123)
- **Redis**: localhost:6379

## Performance Optimization

The setup is optimized for a server with limited resources (3 cores, 16GB RAM):

### Build Optimization
- Multi-stage caching in Dockerfile
- Only copies necessary files initially
- Builds dependencies layer separately from code

### Runtime Optimization
- Neo4j memory limited to 1GB heap, 512MB page cache
- Celery worker concurrency set to 2
- Redis with minimal memory footprint
- Health checks to ensure services start in correct order

### Network Optimization
- Services communicate via Docker internal network
- No unnecessary network: host usage

## Troubleshooting

### Build Takes Too Long
If the initial build is slow, this is normal for the first time due to downloading Python packages. Subsequent builds should be much faster due to layer caching.

To rebuild only a specific service:
```bash
docker-compose build backend_2
```

### Service Won't Start
Check the logs:
```bash
docker-compose logs backend_2
docker-compose logs backend_2_worker
docker-compose logs neo4j
docker-compose logs redis
```

Restart a specific service:
```bash
docker-compose restart backend_2
```

### Out of Memory
If you encounter memory issues, you can further reduce Neo4j memory:
```yaml
# In docker-compose.yml, adjust Neo4j environment:
- NEO4J_dbms_memory_heap_max__size=768M
- NEO4J_dbms_memory_pagecache_size=384M
```

### Database Issues
If you need to reset the database while keeping media files:
```bash
docker-compose down
docker volume rm shrecknet_backend-data
docker-compose up -d
```

## Environment Variables

Default environment variables are set in docker-compose.yml. To override:

1. Create a `.env` file in the root directory
2. Add variables with the `BACKEND_2_` prefix:
   ```
   BACKEND_2_DEBUG=true
   BACKEND_2_OPENAI_API_KEY=your-key-here
   ```

## Development Mode

For development, you can mount the code directory instead of copying:

```yaml
# Add to backend_2 service in docker-compose.yml:
volumes:
  - ./backend_2/app:/app/app
```

Then restart the service when code changes.

## Security Notes

- Change the default Neo4j password in production
- Use secrets management for sensitive environment variables
- The media folder is publicly accessible - ensure proper access controls in production
- Consider using reverse proxy (nginx) for HTTPS in production
