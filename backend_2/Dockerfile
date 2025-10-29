FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only pyproject.toml first for better layer caching
COPY pyproject.toml .

# Install dependencies from pyproject.toml
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -e .[test]

# Now copy the rest of the application code
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
