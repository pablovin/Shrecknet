#!/bin/bash
# Quick Start Guide for Docker Compose

echo "=================================================="
echo "  Shrecknet Backend_2 Docker Quick Start"
echo "=================================================="
echo ""

# Step 1: Validate
echo "Step 1: Validating Docker setup..."
./validate-docker.sh
if [ $? -ne 0 ]; then
    echo "❌ Validation failed. Please fix the issues above."
    exit 1
fi

echo ""
echo "=================================================="
echo "  Ready to Start!"
echo "=================================================="
echo ""
echo "To start all services:"
echo "  docker compose up -d"
echo ""
echo "To view logs:"
echo "  docker compose logs -f"
echo ""
echo "To check service status:"
echo "  docker compose ps"
echo ""
echo "Once running, access:"
echo "  • Backend API: http://localhost:8000"
echo "  • API Docs: http://localhost:8000/docs"
echo "  • Media Files: http://localhost:8000/media/"
echo "  • Neo4j Browser: http://localhost:7474"
echo "    (username: neo4j, password: VeryStrongPass123)"
echo ""
echo "To stop services:"
echo "  docker compose down"
echo ""
echo "To stop and remove all data (WARNING!):"
echo "  docker compose down -v"
echo ""
echo "=================================================="
echo ""
echo "First build may take 15-30 minutes."
echo "Subsequent builds will be much faster (30-60s)."
echo ""
echo "For detailed documentation, see DOCKER.md"
echo "=================================================="
