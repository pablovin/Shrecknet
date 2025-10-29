FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml for pip install
COPY pyproject.toml .

# Install dependencies
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -e .[test]

# Now copy the rest of your code
COPY . .

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
