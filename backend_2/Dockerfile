FROM python:3.11-slim

WORKDIR /app

# Install system dependencies - add more needed for ML packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files for layer caching
COPY pyproject.toml .
COPY app/__init__.py app/__init__.py

# Upgrade pip and install build tools
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Install dependencies (not in editable mode to avoid issues)
RUN pip install --no-cache-dir .[test]

# Now copy the rest of the application code
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
