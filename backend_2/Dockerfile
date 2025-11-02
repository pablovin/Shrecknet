e pip downloads between builds.

# syntax=docker/dockerfile:1.7-labs
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

# --- cache-friendly: only metadata first
COPY pyproject.toml README.md ./
RUN python -m pip install --upgrade "pip>=24.3.1" setuptools wheel

ARG EXTRAS=ml
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# pre-install torch only when ml, with pip cache
RUN if [ "$EXTRAS" = "ml" ]; then \
      --mount=type=cache,target=/root/.cache/pip \
      pip install --prefer-binary --index-url "$TORCH_INDEX_URL" "torch>=2,<3"; \
    fi

# now copy source (invalidates only when code changes)
COPY . .

# install project (tests + optional ml), with pip cache
RUN if [ "$EXTRAS" = "ml" ]; then \
      --mount=type=cache,target=/root/.cache/pip \
      pip install ".[test,ml]"; \
    else \
      --mount=type=cache,target=/root/.cache/pip \
      pip install ".[test]"; \
    fi

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]