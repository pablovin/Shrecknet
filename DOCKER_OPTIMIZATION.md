# Docker Optimization Summary

This document explains all the optimizations made to ensure fast, efficient Docker deployment on a server with limited resources (3 CPU cores, 16GB RAM).

## Problem Statement Issues Resolved

### 1. SQLAlchemy Installation Error
**Problem**: `ERROR: No matching distribution found for SQLAlchemy<2.1,>=2.0`

**Root Cause**: The Dockerfile used editable install (`pip install -e .`) which requires the full source code to be present, but only `pyproject.toml` was copied initially.

**Solution**:
- Changed from `pip install -e .[test]` to `pip install .[test]`
- Copy minimal package structure (`app/__init__.py`) before installing dependencies
- This allows pip to properly resolve and install all dependencies

### 2. Slow Build Times (3+ hours)
**Problem**: Docker builds were taking ~3 hours due to large ML dependencies.

**Solutions Implemented**:

#### a. Multi-layer Caching Strategy
```dockerfile
# Copy only dependency files first
COPY pyproject.toml .
COPY app/__init__.py app/__init__.py

# Install dependencies in separate layer
RUN pip install --no-cache-dir .[test]

# Copy application code last
COPY . .
```
This ensures dependency installation is cached and only rebuilds when `pyproject.toml` changes.

#### b. Optimized Package Installation
- Install `setuptools` and `wheel` first for faster builds
- Use `--no-cache-dir` to avoid storing pip cache in image layers
- Added `g++` compiler needed for certain ML package compilation

#### c. Minimal System Dependencies
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*
```
Only install essential build tools, then clean up apt cache immediately.

### 3. Resource Optimization for Limited Server

#### Neo4j Memory Tuning
```yaml
environment:
  - NEO4J_PLUGINS=["apoc"]                         # Only APOC, removed GDS for performance
  - NEO4J_dbms_memory_pagecache_size=512M      # Default would be 2G+
  - NEO4J_dbms_memory_heap_initial__size=512M
  - NEO4J_dbms_memory_heap_max__size=1G        # Default would be 4G+
  - NEO4J_dbms_jvm_additional=-XX:+UseG1GC    # Better GC for limited memory
```

**Impact**: 
- Reduces Neo4j memory footprint from ~6GB to ~1.5GB while maintaining reasonable performance
- Removed `graph-data-science` plugin to save ~500MB-1GB memory
- APOC plugin provides essential procedures without heavy resource requirements

#### Celery Worker Concurrency
```yaml
command: celery -A app.celery_app worker --loglevel=info --concurrency=2
```

**Impact**: Limits concurrent task processing to 2 workers (matches available CPU cores), preventing CPU thrashing.

#### Redis Optimization
```yaml
command: redis-server --save 60 1 --loglevel warning
```

**Impact**: 
- Minimal logging reduces I/O overhead
- Periodic saves (every 60s if at least 1 key changed) balances persistence with performance

### 4. Data Persistence Requirements

All data persists across container restarts using Docker named volumes:

```yaml
volumes:
  redis-data:         # Redis database
  neo4j-data:         # Neo4j graph database
  neo4j-logs:         # Neo4j logs
  neo4j-import:       # Neo4j import directory
  neo4j-plugins:      # Neo4j plugins
  backend-media:      # User-uploaded media files
  backend-data:       # SQLite databases (backend_2.db, backend_2_jobs.db)
```

**Benefits**:
- Data survives `docker compose down`
- Data can be backed up independently
- Volumes can be mounted on different hosts
- Only deleted with explicit `docker compose down -v`

### 5. Media Folder Accessibility

```yaml
# In backend_2 service:
ports:
  - "8000:8000"
volumes:
  - backend-media:/app/media

# Application mounts media at /media endpoint
```

**Result**: Media files accessible at `http://server:8000/media/` from anywhere with network access to the server.

## Performance Characteristics

### Expected Build Times
- **First build**: 15-30 minutes (downloading and compiling ML packages)
- **Subsequent builds** (code changes only): 30-60 seconds
- **Rebuild after dependency change**: 15-30 minutes

### Memory Usage
- **Total expected**: ~4-6GB RAM
  - Neo4j: ~1.5GB
  - backend_2: ~1-2GB (ML models)
  - backend_2_worker: ~1-2GB (ML models)
  - Redis: ~50-100MB
  - Docker overhead: ~500MB

### Disk Usage
- **Docker images**: ~3-4GB
- **Volumes** (depends on usage):
  - neo4j-data: ~100MB-10GB (depends on graph size)
  - backend-data: ~10MB-1GB (SQLite databases)
  - backend-media: Depends on uploaded files

## Service Startup Order

Health checks ensure services start in the correct order:

1. **Redis** → Health check via `redis-cli ping`
2. **Neo4j** → Health check via `cypher-shell`
3. **backend_2** → Waits for Redis and Neo4j, health check via `/health` endpoint
4. **backend_2_worker** → Waits for all above services

**Benefit**: Prevents connection errors during startup.

## Network Architecture

Services communicate via Docker's internal network:
- No `network: host` (insecure and causes port conflicts)
- Services reference each other by name (e.g., `redis://redis:6379`)
- Only essential ports exposed to host:
  - 8000: Backend API
  - 6379: Redis (for external monitoring/debugging)
  - 7474, 7687: Neo4j browser and Bolt protocol

## Best Practices Implemented

1. **Restart Policy**: `restart: unless-stopped` ensures services recover from crashes
2. **Health Checks**: Proper monitoring of service health
3. **Minimal Images**: Use `python:3.11-slim` instead of full Python image
4. **Layer Optimization**: Order Dockerfile commands to maximize cache hits
5. **Volume Management**: Named volumes instead of bind mounts for production data
6. **Security**: No hardcoded secrets in code (use environment variables)
7. **Resource Limits**: Explicit memory limits for resource-hungry services

## Monitoring and Debugging

### View Logs
```bash
docker compose logs -f                    # All services
docker compose logs -f backend_2          # Specific service
docker compose logs --tail=100 backend_2  # Last 100 lines
```

### Check Service Health
```bash
docker compose ps
```

### Access Neo4j Browser
Navigate to `http://localhost:7474` and login with:
- Username: `neo4j`
- Password: `VeryStrongPass123`

### Check Volume Usage
```bash
docker volume ls
docker volume inspect shrecknet_backend-media
```

## Future Optimization Opportunities

If you need even better performance:

1. **Use PostgreSQL instead of SQLite** for better concurrent access
2. **Add Redis caching** for frequently accessed data
3. **Implement CDN** for media files
4. **Use prebuilt ML model images** to avoid rebuilding
5. **Add Nginx reverse proxy** for load balancing and caching
6. **Separate Celery queues** by priority
7. **Add monitoring** (Prometheus + Grafana)

## Troubleshooting Common Issues

### Build Fails with Network Timeout
If pip install times out:
```dockerfile
# Add to Dockerfile before pip install:
ENV PIP_DEFAULT_TIMEOUT=100
```

### Out of Memory During Build
Use Docker BuildKit with smaller cache:
```bash
DOCKER_BUILDKIT=1 docker compose build --no-cache backend_2
```

### Services Start Too Slowly
Increase health check intervals:
```yaml
healthcheck:
  start_period: 120s  # Give more time for ML models to load
```

### Volume Permissions Issues
If you encounter permission errors:
```bash
sudo chown -R 1000:1000 $(docker volume inspect shrecknet_backend-media -f '{{.Mountpoint}}')
```
