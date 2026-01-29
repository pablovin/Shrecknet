# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Tools for healthcheck and common wheels (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only files needed to resolve deps first (cache-friendly)
COPY pyproject.toml ./

# Keep pip toolchain fresh to dodge resolver bugs
RUN python -m pip install --upgrade "pip>=24.3.1" setuptools wheel

ARG EXTRAS=
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# Pre-install torch (only for ML image) from the official CPU index
RUN if [ "$EXTRAS" = "ml" ]; then \
      echo "Installing torch from $TORCH_INDEX_URL..." && \
      pip install --prefer-binary --index-url "$TORCH_INDEX_URL" "torch>=2,<3"; \
    fi

# Now copy the source (invalidates layer only when code changes)
COPY . .

# Install your project (test + optional ml)
RUN if [ "$EXTRAS" = "ml" ]; then \
      pip install ".[test,ml]"; \
    else \
      pip install ".[test]"; \
    fi

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]