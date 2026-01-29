# Lightning-Fast Docker Deployment with .venv

This guide explains how to achieve **lightning-fast Docker builds** (~10-30 seconds) by using a pre-built .venv folder instead of installing dependencies from scratch.

## The Problem

Traditional Docker builds install Python dependencies from scratch every time, which can take:
- **Without ML**: 5-10 minutes
- **With ML (PyTorch, sentence-transformers)**: 15-30 minutes or longer
- **On slow networks**: Can exceed 1 hour

This is frustrating during development and deployment, especially when you haven't changed any dependencies.

## The Solution

**Pre-build your .venv folder** with all dependencies installed, then copy it into Docker images. This reduces build time to **~10-30 seconds**.

## Quick Start

### Step 1: Build the .venv

In the `backend` directory, run:

```bash
# For API-only mode (no ML dependencies)
./build-venv.sh

# For ML mode (includes PyTorch, sentence-transformers)
./build-venv.sh --ml
```

This creates a `.venv` folder with all dependencies pre-installed. **This step takes 15-30 minutes**, but you only do it once (or when dependencies change).

### Step 2: Build Docker Images

Now your Docker builds will be **lightning fast**:

```bash
# Build backend_2 API (uses the .venv you just created)
docker compose build backend_2

# Build backend_2 worker with ML (uses the .venv you just created)
docker compose build backend_2_worker
```

**Build time: ~10-30 seconds** ✨

### Step 3: Deploy

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# Check service status
docker compose ps
```

## How It Works

### Traditional Approach (SLOW)
```
Docker Build → Install Python → Download packages → Compile → Done
               ⏱️ 15-30 minutes every build
```

### .venv Approach (FAST)
```
Build .venv once → Copy .venv into Docker → Done
⏱️ 15-30 min       ⏱️ 10-30 seconds
(one-time)         (every build)
```

### Technical Details

The optimized `Dockerfile` works in two modes:

**Mode 1: .venv exists** (FAST)
1. Copies the entire `.venv` folder into the image
2. Sets up PATH to use the pre-installed packages
3. Verifies the venv is valid
4. Skips dependency installation
5. **Total time: ~10-30 seconds**

**Mode 2: .venv doesn't exist** (FALLBACK)
1. Falls back to traditional pip install
2. Installs dependencies from scratch
3. Uses layer caching where possible
4. **Total time: ~15-30 minutes**

## When to Rebuild .venv

Rebuild your `.venv` when:
- ✅ You add/remove/update dependencies in `pyproject.toml`
- ✅ You upgrade Python version
- ✅ You switch between API and ML modes
- ✅ Packages have been updated upstream and you want latest versions

You do **NOT** need to rebuild `.venv` when:
- ❌ You change application code
- ❌ You update configuration
- ❌ You add environment variables

## Deployment Workflows

### Development Workflow

```bash
# One-time setup
cd backend
./build-venv.sh --ml

# Daily development (code changes only)
docker compose build backend_2      # ~10-30 seconds
docker compose up -d backend_2      # ~5 seconds
docker compose logs -f backend_2

# When dependencies change
./build-venv.sh --ml                # ~15-30 minutes
docker compose build backend_2      # ~10-30 seconds
```

### CI/CD Workflow

```yaml
# Example GitHub Actions workflow
steps:
  # Cache the .venv to avoid rebuilding
  - uses: actions/cache@v3
    with:
      path: backend/.venv
      key: venv-${{ hashFiles('backend/pyproject.toml') }}-ml

  # Build .venv if cache miss
  - name: Build venv
    run: |
      cd backend
      if [ ! -d ".venv" ]; then
        ./build-venv.sh --ml
      fi

  # Docker build is now fast
  - name: Build Docker images
    run: docker compose build
```

### Production Deployment

**Option A: Pre-build .venv on deployment server**
```bash
# On your server, one-time setup
cd /path/to/Shrecknet/backend
./build-venv.sh --ml

# Deploy (fast builds)
docker compose build
docker compose up -d
```

**Option B: Include .venv in your repository** (for smaller projects)
```bash
# In backend directory
./build-venv.sh --ml
git add .venv
git commit -m "Add pre-built .venv for fast deployment"
git push

# On server
git pull
docker compose up -d --build  # Super fast!
```

**Option C: Build .venv in CI, upload as artifact**
```bash
# In CI
./build-venv.sh --ml
tar -czf venv.tar.gz .venv
# Upload to artifact store

# On server
curl -o venv.tar.gz https://artifacts.example.com/venv.tar.gz
tar -xzf venv.tar.gz
docker compose up -d --build
```

## Performance Comparison

| Scenario | Without .venv | With .venv | Speedup |
|----------|--------------|------------|---------|
| API only (first build) | ~5-10 min | ~10-30 sec | **10-20x faster** |
| ML mode (first build) | ~15-30 min | ~10-30 sec | **30-60x faster** |
| Code-only change | ~30-60 sec | ~10-30 sec | **2-3x faster** |
| Dependency change | ~15-30 min | ~15-30 min + ~10-30 sec | Same (rebuild .venv) |

## Best Practices

### 1. Version Control

Add `.venv` to `.gitignore` for large repositories:
```gitignore
# Don't commit .venv for large projects
.venv/
```

Or commit it for smaller projects where fast deployment matters more than repo size:
```bash
# Commit .venv for instant deployments
git add .venv
git commit -m "Add pre-built dependencies"
```

### 2. CI/CD Caching

Use CI/CD caching to avoid rebuilding `.venv`:
```yaml
# Cache based on pyproject.toml hash
cache:
  paths:
    - backend/.venv
  key: $CI_COMMIT_REF_SLUG-venv-${{ hashFiles('backend/pyproject.toml') }}
```

### 3. Separate API and ML venvs

For different services:
```bash
# Build separate venvs
./build-venv.sh          # API mode → .venv-api
./build-venv.sh --ml     # ML mode → .venv-ml

# Use in Dockerfile with build args
docker compose build --build-arg VENV_PATH=.venv-api backend_2
docker compose build --build-arg VENV_PATH=.venv-ml backend_2_worker
```

### 4. Health Checks

The optimized Dockerfile includes automatic validation:
- Checks if `.venv/bin/python` exists
- Verifies Python version
- Falls back to fresh install if .venv is corrupted

## Troubleshooting

### .venv not being used

**Symptom**: Build still takes 15-30 minutes even with .venv

**Solutions**:
1. Check `.venv` exists: `ls -la backend/.venv`
2. Check `.dockerignore`: Ensure `.venv` is **not** excluded
3. Rebuild .venv: `cd backend && ./build-venv.sh --ml`
4. Check Docker logs: `docker compose build backend_2 2>&1 | grep -i venv`

### Build fails with "python: command not found"

**Symptom**: Docker build fails when trying to use .venv

**Solutions**:
1. Rebuild .venv with correct Python version: `python3.11 -m venv .venv`
2. Ensure .venv is from compatible Python (3.11)
3. Check Dockerfile FROM line matches your Python version

### Permission errors

**Symptom**: Docker can't access .venv files

**Solutions**:
```bash
# Fix ownership
sudo chown -R $USER:$USER backend/.venv

# Fix permissions
chmod -R 755 backend/.venv
```

### Different behavior in .venv vs Docker

**Symptom**: Code works in .venv but not in Docker

**Solutions**:
1. Ensure same Python version: `python --version` vs Docker Python
2. Check environment variables are set correctly
3. Verify all system dependencies are installed in Dockerfile

### .venv is too large

**Symptom**: .venv folder is 2-3 GB

**Solution**: This is normal for ML mode (PyTorch is large). Consider:
1. Use `.venv-api` (smaller) for API-only services
2. Don't commit .venv to git, build in CI instead
3. Use Docker layer caching instead of .venv for very large projects

## Architecture

### Services

All services now deploy with optimized builds:

1. **backend_2** (API mode)
   - Uses `.venv` without ML dependencies
   - Build time: ~10-30 seconds
   - Image size: ~1-2 GB

2. **backend_2_worker** (ML mode)
   - Uses `.venv` with ML dependencies
   - Build time: ~10-30 seconds (with pre-built .venv)
   - Image size: ~3-4 GB

3. **neo4j** - Graph database (unchanged)
4. **redis** - Message broker (unchanged)

### Volumes

All data persists across deployments:
- `backend-media`: User uploads
- `backend-data`: SQLite databases
- `neo4j-data`: Graph database
- `redis-data`: Message queue

## Advanced Usage

### Custom venv location

```bash
# Build venv in custom location
python3 -m venv /path/to/custom-venv
source /path/to/custom-venv/bin/activate
pip install -e .[ml]

# Copy to backend_2
cp -r /path/to/custom-venv backend/.venv

# Build Docker
docker compose build
```

### Multi-architecture builds

```bash
# Build .venv for your architecture
./build-venv.sh --ml

# Build Docker for multiple architectures
docker buildx build --platform linux/amd64,linux/arm64 -t backend_2 .
```

### Inspecting the .venv

```bash
# Check installed packages
.venv/bin/pip list

# Check Python version
.venv/bin/python --version

# Activate venv manually
source .venv/bin/activate
python -c "import torch; print(torch.__version__)"
```

## Summary

| Action | Command | Time |
|--------|---------|------|
| Initial .venv build | `./build-venv.sh --ml` | 15-30 min (one-time) |
| Docker build (with .venv) | `docker compose build` | **10-30 sec** ⚡ |
| Docker build (without .venv) | `docker compose build` | 15-30 min ❌ |
| Deploy services | `docker compose up -d` | 10-30 sec |
| Code change rebuild | `docker compose build` | **10-30 sec** ⚡ |

**Bottom line**: Spend 15-30 minutes once to build .venv, then enjoy **10-30 second builds forever** (until dependencies change).
