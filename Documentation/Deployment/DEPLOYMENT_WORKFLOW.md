# Backend_2 Deployment Workflow

This document provides complete deployment workflows for different scenarios.

## Quick Reference

| Scenario | Build Time | Steps |
|----------|-----------|--------|
| **Initial deployment with .venv** | 15-30 min (one-time) + 10-30 sec | Build .venv → Build Docker → Deploy |
| **Code changes with .venv** | 10-30 sec | Build Docker → Deploy |
| **Dependency changes with .venv** | 15-30 min + 10-30 sec | Rebuild .venv → Build Docker → Deploy |
| **Initial deployment without .venv** | 15-30 min | Build Docker → Deploy |
| **Code changes without .venv** | 30-60 sec | Build Docker → Deploy |

## Deployment Workflows

### Workflow 1: Initial Deployment (Fast Mode - Recommended)

Use this for the first-time deployment with maximum speed.

```bash
# Step 1: Clone repository
git clone https://github.com/pablovin/Shrecknet.git
cd Shrecknet

# Step 2: Build .venv (ONE-TIME, takes 15-30 minutes)
cd backend_2
./build-venv.sh --ml

# Step 3: Build Docker images (10-30 seconds!)
cd ..
docker compose build

# Step 4: Start all services
docker compose up -d

# Step 5: Verify deployment
docker compose ps
docker compose logs -f backend_2

# Access the API
# http://localhost:8000/docs
```

**Total time**: ~15-30 minutes (one-time setup)
**Subsequent deploys**: ~10-30 seconds

### Workflow 2: Code Changes (Fast Mode)

Use this when you've changed application code but not dependencies.

```bash
# Step 1: Pull latest code
git pull

# Step 2: Rebuild Docker images (10-30 seconds!)
docker compose build

# Step 3: Restart services
docker compose up -d

# Step 4: Verify
docker compose logs -f backend_2
```

**Total time**: ~10-30 seconds ⚡

### Workflow 3: Dependency Changes (Fast Mode)

Use this when you've updated pyproject.toml or need new packages.

```bash
# Step 1: Pull latest code
git pull

# Step 2: Rebuild .venv (15-30 minutes)
cd backend_2
./build-venv.sh --ml

# Step 3: Rebuild Docker images (10-30 seconds)
cd ..
docker compose build

# Step 4: Restart services
docker compose up -d

# Step 5: Verify
docker compose logs -f backend_2
```

**Total time**: ~15-30 minutes (rebuild .venv) + 10-30 seconds

### Workflow 4: Initial Deployment (Standard Mode)

Use this if you don't want to use .venv pre-building.

```bash
# Step 1: Clone repository
git clone https://github.com/pablovin/Shrecknet.git
cd Shrecknet

# Step 2: Build Docker images (15-30 minutes)
docker compose build

# Step 3: Start all services
docker compose up -d

# Step 4: Verify deployment
docker compose ps
docker compose logs -f backend_2
```

**Total time**: ~15-30 minutes
**Subsequent code-only deploys**: ~30-60 seconds

### Workflow 5: Production Deployment on Server

Complete production deployment workflow.

```bash
# Step 1: SSH into server
ssh user@server.example.com

# Step 2: Clone or pull repository
git clone https://github.com/pablovin/Shrecknet.git
# OR
cd Shrecknet && git pull

# Step 3: Build .venv for production (ONE-TIME)
cd backend_2
./build-venv.sh --ml

# Step 4: Configure environment (if needed)
cd ..
cp .env.example .env  # If you have environment file
nano .env             # Edit configuration

# Step 5: Build and start services
docker compose build
docker compose up -d

# Step 6: Check service health
docker compose ps
docker compose logs --tail=50 backend_2
docker compose logs --tail=50 backend_2_worker

# Step 7: Test API endpoint
curl http://localhost:8000/health
curl http://localhost:8000/docs

# Step 8: Set up reverse proxy (optional but recommended)
# Configure nginx/caddy to proxy to localhost:8000
# Enable HTTPS with Let's Encrypt

# Step 9: Set up log rotation
docker compose logs --since=1h > /var/log/shrecknet/backend_2.log

# Step 10: Enable monitoring (optional)
# Set up Prometheus, Grafana, or your monitoring solution
```

### Workflow 6: CI/CD Pipeline

Example GitHub Actions workflow for automated deployment.

```yaml
name: Deploy Backend_2

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      # Cache .venv to avoid rebuilding
      - name: Cache .venv
        uses: actions/cache@v3
        with:
          path: backend_2/.venv
          key: venv-${{ hashFiles('backend_2/pyproject.toml') }}-ml
      
      # Build .venv if cache miss
      - name: Build .venv
        run: |
          cd backend_2
          if [ ! -d ".venv" ]; then
            ./build-venv.sh --ml
          fi
      
      # Build Docker images (fast with .venv!)
      - name: Build Docker images
        run: docker compose build
      
      # Deploy to server
      - name: Deploy to server
        run: |
          # Copy .venv and code to server
          rsync -avz backend_2/.venv server:/path/to/Shrecknet/backend_2/
          rsync -avz . server:/path/to/Shrecknet/ --exclude .venv
          
          # Rebuild and restart on server
          ssh server "cd /path/to/Shrecknet && docker compose up -d --build"
      
      # Verify deployment
      - name: Health check
        run: |
          sleep 30  # Wait for services to start
          curl -f http://server:8000/health || exit 1
```

### Workflow 7: Rolling Update (Zero Downtime)

For production updates without downtime.

```bash
# Step 1: Build new images
docker compose build backend_2 backend_2_worker

# Step 2: Scale up new instances
docker compose up -d --scale backend_2=2 --no-recreate

# Step 3: Wait for health checks
sleep 30

# Step 4: Remove old instances
docker compose up -d --scale backend_2=1 --force-recreate backend_2

# Step 5: Verify
docker compose ps
docker compose logs --tail=20 backend_2
```

### Workflow 8: Rollback

Quick rollback to previous version.

```bash
# Step 1: Check current version
git log --oneline -1

# Step 2: Rollback code
git checkout <previous-commit-sha>

# Step 3: Rebuild (fast with .venv)
docker compose build

# Step 4: Restart services
docker compose up -d --force-recreate

# Step 5: Verify
docker compose logs -f backend_2
```

## Maintenance Tasks

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend_2

# Last 100 lines
docker compose logs --tail=100 backend_2

# Since specific time
docker compose logs --since=1h backend_2
```

### Restart Services

```bash
# Restart all
docker compose restart

# Restart specific service
docker compose restart backend_2

# Full rebuild and restart
docker compose up -d --build --force-recreate
```

### Database Backup

```bash
# Backup SQLite databases
docker compose exec backend_2 tar -czf /tmp/backup.tar.gz /data/*.db
docker compose cp backend_2:/tmp/backup.tar.gz ./backup-$(date +%Y%m%d).tar.gz

# Backup Neo4j
docker compose exec neo4j neo4j-admin dump --to=/data/backup.dump
docker compose cp neo4j:/data/backup.dump ./neo4j-backup-$(date +%Y%m%d).dump
```

### Database Restore

```bash
# Restore SQLite
docker compose cp ./backup-20231201.tar.gz backend_2:/tmp/backup.tar.gz
docker compose exec backend_2 tar -xzf /tmp/backup.tar.gz -C /

# Restore Neo4j
docker compose stop neo4j
docker compose cp ./neo4j-backup-20231201.dump neo4j:/data/backup.dump
docker compose exec neo4j neo4j-admin load --from=/data/backup.dump --force
docker compose start neo4j
```

### Clean Up

```bash
# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes all data!)
docker compose down -v

# Clean up unused Docker resources
docker system prune -a

# Clean up specific .venv
cd backend_2
rm -rf .venv
```

### Monitoring

```bash
# Check resource usage
docker stats

# Check service health
docker compose ps

# Check specific container
docker inspect backend_2

# Check volumes
docker volume ls
docker volume inspect shrecknet_backend-data
```

## Troubleshooting

### Build is slow even with .venv

**Check**:
```bash
# Verify .venv exists
ls -la backend_2/.venv

# Check .dockerignore doesn't exclude .venv
grep -v "^#" backend_2/.dockerignore | grep venv

# Rebuild .venv
cd backend_2
rm -rf .venv
./build-venv.sh --ml
```

### Services won't start

**Debug**:
```bash
# Check logs
docker compose logs backend_2

# Check health
docker compose ps

# Restart with fresh build
docker compose down
docker compose up -d --build
```

### Out of memory

**Fix**:
```bash
# Reduce Neo4j memory in docker-compose.yml
# Change: NEO4J_dbms_memory_heap_max__size=768M

# Restart
docker compose up -d neo4j
```

### Port already in use

**Fix**:
```bash
# Check what's using port 8000
sudo lsof -i :8000

# Change port in docker-compose.yml
# ports:
#   - "8001:8000"  # Map to different host port
```

## Performance Optimization Checklist

- [x] Use .venv for fast builds (~10-30 seconds)
- [ ] Enable Docker BuildKit: `export DOCKER_BUILDKIT=1`
- [ ] Use Docker layer caching in CI/CD
- [ ] Configure reverse proxy (nginx/caddy) for production
- [ ] Set up CDN for media files
- [ ] Enable Redis caching for frequently accessed data
- [ ] Use PostgreSQL instead of SQLite for better concurrency
- [ ] Set up load balancing for multiple backend_2 instances
- [ ] Configure monitoring (Prometheus + Grafana)
- [ ] Set up automated backups
- [ ] Enable HTTPS with Let's Encrypt
- [ ] Configure log rotation
- [ ] Set resource limits in docker-compose.yml

## Security Checklist

- [ ] Change default Neo4j password
- [ ] Use environment variables for secrets (not hardcoded)
- [ ] Enable HTTPS in production
- [ ] Configure firewall rules (only expose necessary ports)
- [ ] Set up fail2ban for brute force protection
- [ ] Regular security updates: `docker compose pull && docker compose up -d`
- [ ] Enable audit logging
- [ ] Configure backup encryption
- [ ] Set up intrusion detection
- [ ] Regular security scans: `docker scan`

## Support

For issues or questions:
- Check [DOCKER.md](../DOCKER.md) for general Docker info
- Check [VENV_DEPLOYMENT.md](../VENV_DEPLOYMENT.md) for .venv details
- Check [DOCKER_OPTIMIZATION.md](../DOCKER_OPTIMIZATION.md) for optimization details
- Open an issue on GitHub
