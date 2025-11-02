#!/bin/bash
# Build a .venv folder for fast Docker deployment
# This script creates a virtual environment with all dependencies pre-installed
# The resulting .venv can be copied into Docker builds for ~10-30 second build times

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Parse arguments
EXTRAS=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --ml)
      EXTRAS="ml"
      shift
      ;;
    --help)
      echo "Usage: $0 [--ml]"
      echo ""
      echo "Options:"
      echo "  --ml     Include ML dependencies (sentence-transformers, torch)"
      echo "  --help   Show this help message"
      echo ""
      echo "This script builds a .venv folder with all dependencies installed."
      echo "The .venv can then be used by Docker for lightning-fast builds."
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Use --help for usage information"
      exit 1
      ;;
  esac
done

echo "==> Building .venv for backend_2"
echo ""

# Remove existing .venv if it exists
if [ -d ".venv" ]; then
  echo "Removing existing .venv..."
  rm -rf .venv
fi

# Create new virtual environment
echo "Creating virtual environment..."
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade "pip<25.4"

# Install base dependencies
echo "Installing base dependencies..."
pip install .[test]

# Install ML dependencies if requested
if [ "$EXTRAS" = "ml" ]; then
  echo ""
  echo "Installing ML dependencies (this may take 10-20 minutes)..."
  pip install --prefer-binary \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    ".[ml]"
fi

# Show installed packages
echo ""
echo "==> Installed packages:"
pip list

echo ""
echo "==> .venv build complete!"
echo ""
if [ "$EXTRAS" = "ml" ]; then
  echo "Your .venv includes ML dependencies and is ready for Docker deployment."
else
  echo "Your .venv is ready for Docker deployment (API mode only)."
  echo "To include ML dependencies, run: $0 --ml"
fi
echo ""
echo "Docker build will now take ~10-30 seconds instead of 15-30 minutes!"
echo ""
echo "To use this .venv with Docker:"
echo "  docker compose build backend_2        # For API mode"
echo "  docker compose build backend_2_worker # For ML worker mode"
echo ""
