FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy application code first
COPY . .

# Install dependencies directly from pyproject.toml
RUN pip install --upgrade pip && \
    pip install --no-cache-dir \
    'fastapi>=0.110,<0.111' \
    'uvicorn[standard]>=0.27,<0.28' \
    'SQLAlchemy>=2.0,<2.1' \
    'pydantic>=2.6,<3' \
    'pydantic-settings>=2.2,<3' \
    'alembic>=1.13,<1.14' \
    'python-jose[cryptography]>=3.3,<4' \
    'passlib[argon2]>=1.7,<2' \
    'email-validator>=2.1,<3' \
    'python-multipart>=0.0.9,<1' \
    'Pillow>=10,<11' \
    'neo4j>=5.13,<6' \
    'celery>=5.4,<6' \
    'sentence-transformers>=2.2,<3' \
    'aiosqlite>=0.19' \
    'langchain>=0.1,<0.4' \
    'langchain-openai>=0.0.5,<0.3' \
    'langgraph>=0.0.26,<0.3' \
    'openai>=1.0,<2' \
    'pytest>=7.4' \
    'httpx>=0.25' \
    'pytest-asyncio>=0.21'

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
