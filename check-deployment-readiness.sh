#!/bin/bash
# Validate backend_2 deployment readiness
# This script checks if you're ready to deploy

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend_2"

echo "========================================="
echo "Backend_2 Deployment Readiness Check"
echo "========================================="
echo ""

# Check if .venv exists
if [ -d ".venv" ] && [ -f ".venv/bin/python" ]; then
  echo "✅ .venv found - FAST BUILD MODE (10-30 seconds)"
  VENV_SIZE=$(du -sh .venv | cut -f1)
  echo "   Size: $VENV_SIZE"
  PYTHON_VERSION=$(.venv/bin/python --version 2>&1)
  echo "   Python: $PYTHON_VERSION"
  
  # Check if ML packages are installed
  if .venv/bin/python -c "import sentence_transformers" 2>/dev/null; then
    echo "   Mode: ML (includes PyTorch, sentence-transformers)"
  else
    echo "   Mode: API only (no ML packages)"
  fi
else
  echo "⚠️  .venv not found - STANDARD BUILD MODE (15-30 minutes)"
  echo "   To enable fast builds, run:"
  echo "   cd backend_2 && ./build-venv.sh --ml"
fi

echo ""

# Check Dockerfile
if [ -f "Dockerfile" ]; then
  echo "✅ Dockerfile found"
else
  echo "❌ Dockerfile not found"
  exit 1
fi

# Check pyproject.toml
if [ -f "pyproject.toml" ]; then
  echo "✅ pyproject.toml found"
else
  echo "❌ pyproject.toml not found"
  exit 1
fi

# Check build-venv.sh
if [ -f "build-venv.sh" ] && [ -x "build-venv.sh" ]; then
  echo "✅ build-venv.sh found and executable"
else
  echo "⚠️  build-venv.sh not executable"
  echo "   Run: chmod +x build-venv.sh"
fi

echo ""

# Check docker-compose.yml
cd ..
if [ -f "docker-compose.yml" ]; then
  echo "✅ docker-compose.yml found"
  
  # Validate syntax
  if docker compose config > /dev/null 2>&1; then
    echo "✅ docker-compose.yml syntax valid"
    
    # Check services
    SERVICES=$(docker compose config --services)
    if echo "$SERVICES" | grep -q "backend_2"; then
      echo "✅ backend_2 service configured"
    else
      echo "❌ backend_2 service not found"
    fi
    
    if echo "$SERVICES" | grep -q "backend_2_worker"; then
      echo "✅ backend_2_worker service configured"
    else
      echo "❌ backend_2_worker service not found"
    fi
    
    if echo "$SERVICES" | grep -q "neo4j"; then
      echo "✅ neo4j service configured"
    else
      echo "❌ neo4j service not found"
    fi
    
    if echo "$SERVICES" | grep -q "redis"; then
      echo "✅ redis service configured"
    else
      echo "❌ redis service not found"
    fi
  else
    echo "❌ docker-compose.yml has syntax errors"
    exit 1
  fi
else
  echo "❌ docker-compose.yml not found"
  exit 1
fi

echo ""
echo "========================================="
echo "Readiness Summary"
echo "========================================="

if [ -d "backend_2/.venv" ]; then
  echo "🚀 FAST BUILD MODE READY"
  echo ""
  echo "You can deploy in ~10-30 seconds:"
  echo "  docker compose build"
  echo "  docker compose up -d"
else
  echo "⏱️  STANDARD BUILD MODE"
  echo ""
  echo "For fast builds (recommended), first run:"
  echo "  cd backend_2"
  echo "  ./build-venv.sh --ml"
  echo "  cd .."
  echo ""
  echo "Then deploy:"
  echo "  docker compose build"
  echo "  docker compose up -d"
fi

echo ""
echo "For more information:"
echo "  - Quick Start: QUICKSTART.md"
echo "  - Complete Guide: VENV_DEPLOYMENT.md"
echo "  - Workflows: DEPLOYMENT_WORKFLOW.md"
echo ""
