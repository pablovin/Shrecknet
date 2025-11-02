# syntax=docker/dockerfile:1.7-labs
FROM python:3.11-slim-bookworm

# ---- sane defaults to avoid stalls ----
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Avoid flaky mirrors & speed apt when we actually need it
RUN printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/99retry

WORKDIR /app

# By default, build the API flavor (no ML). Override via --build-arg EXTRAS=ml
ARG EXTRAS=

# ===== OPTION 1: Use pre-existing .venv (FASTEST) =====
# If .venv exists in the build context, copy it directly
# This is the fastest option: ~10-30 seconds build time
COPY --chown=root:root .venv* /app/.venv/ 2>/dev/null || true

# Check if .venv was copied successfully
RUN if [ -d "/app/.venv" ] && [ -f "/app/.venv/bin/python" ]; then \
      echo "==> Using pre-existing .venv - FAST BUILD MODE" && \
      # Verify the venv is valid
      /app/.venv/bin/python --version && \
      # Set up environment to use this venv
      echo "export PATH=/app/.venv/bin:\$PATH" > /etc/profile.d/venv.sh && \
      ln -sf /app/.venv/bin/python /usr/local/bin/python && \
      ln -sf /app/.venv/bin/pip /usr/local/bin/pip; \
    else \
      echo "==> No .venv found - will install dependencies from scratch"; \
    fi

# ===== OPTION 2: Install from scratch (FALLBACK) =====
# Only runs if .venv doesn't exist or is invalid
RUN if [ ! -d "/app/.venv" ] || [ ! -f "/app/.venv/bin/python" ]; then \
      echo "==> Installing dependencies from scratch" && \
      # Copy metadata first for caching
      pip install "pip<25.4" && \
      mkdir -p app && \
      true; \
    fi

# Copy minimal package structure (only needed for fresh install)
COPY app/__init__.py app/__init__.py 2>/dev/null || true
COPY pyproject.toml ./ 2>/dev/null || true

# Install base dependencies if .venv doesn't exist
RUN if [ ! -d "/app/.venv" ] || [ ! -f "/app/.venv/bin/python" ]; then \
      if [ -z "$EXTRAS" ]; then \
        pip install .[test]; \
      else \
        echo "Defer ML extras to next stage"; \
      fi; \
    fi

# Now copy the rest of the app
COPY . .

# Install ML extras if needed and not using pre-existing .venv with ML already installed
RUN if [ "$EXTRAS" = "ml" ]; then \
      # Check if sentence-transformers is already installed (from .venv)
      if python -c "import sentence_transformers" 2>/dev/null; then \
        echo "==> ML dependencies already installed in .venv"; \
      else \
        echo "==> Installing ML dependencies" && \
        pip install --prefer-binary \
          --extra-index-url https://download.pytorch.org/whl/cpu \
          ".[ml]"; \
      fi; \
    fi

# Ensure PATH includes .venv if it exists
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]