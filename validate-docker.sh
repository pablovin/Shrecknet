#!/bin/bash
# Simple validation script for Docker Compose setup

set -e

echo "========================================="
echo "Docker Compose Configuration Validator"
echo "========================================="
echo ""

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed"
    exit 1
fi
echo "✅ Docker is installed"

# Check if docker compose is available
if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose is not available"
    exit 1
fi
echo "✅ Docker Compose is available"

# Validate docker-compose.yml syntax
echo ""
echo "Validating docker-compose.yml..."
if docker compose config > /dev/null 2>&1; then
    echo "✅ docker-compose.yml syntax is valid"
else
    echo "❌ docker-compose.yml has syntax errors"
    exit 1
fi

# Check if Dockerfile exists
if [ ! -f "backend_2/Dockerfile" ]; then
    echo "❌ backend_2/Dockerfile not found"
    exit 1
fi
echo "✅ backend_2/Dockerfile exists"

# Validate services are defined
echo ""
echo "Checking required services..."
services=$(docker compose config --services)

required_services=("redis" "neo4j" "backend_2" "backend_2_worker")
for service in "${required_services[@]}"; do
    if echo "$services" | grep -q "^${service}$"; then
        echo "✅ Service '$service' is defined"
    else
        echo "❌ Service '$service' is missing"
        exit 1
    fi
done

# Check volumes are defined
echo ""
echo "Checking persistent volumes..."
volumes=$(docker compose config --volumes)

required_volumes=("backend-media" "backend-data" "neo4j-data" "redis-data")
for volume in "${required_volumes[@]}"; do
    if echo "$volumes" | grep -q "^${volume}$"; then
        echo "✅ Volume '$volume' is defined"
    else
        echo "❌ Volume '$volume' is missing"
        exit 1
    fi
done

echo ""
echo "========================================="
echo "✅ All validation checks passed!"
echo "========================================="
echo ""
echo "You can now run:"
echo "  docker compose up -d       # Start all services"
echo "  docker compose logs -f     # View logs"
echo "  docker compose down        # Stop all services"
