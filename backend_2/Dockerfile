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

# ===== Copy all files including optional .venv =====
# The .venv folder will be included if it exists and isn't in .dockerignore
COPY . .

# ===== Check if .venv exists and is valid =====
RUN if [ -d "/app/.venv" ] && [ -f "/app/.venv/bin/python" ]; then \
      echo "========================================" && \
      echo "==> FAST BUILD MODE: Using .venv" && \
      echo "========================================" && \
      /app/.venv/bin/python --version && \
      # Update PATH to prioritize .venv
      echo 'export PATH="/app/.venv/bin:$PATH"' >> /etc/bash.bashrc && \
      ln -sf /app/.venv/bin/python /usr/local/bin/python && \
      ln -sf /app/.venv/bin/pip /usr/local/bin/pip && \
      echo "✓ .venv configured successfully" && \
      # Mark that we're using venv
      touch /app/.using-venv; \
    else \
      echo "========================================" && \
      echo "==> STANDARD BUILD: Installing from scratch" && \
      echo "========================================" && \
      # Upgrade pip first
      pip install "pip<25.4" && \
      # Install base dependencies
      if [ -z "$EXTRAS" ]; then \
        pip install .[test]; \
      else \
        echo "Deferring ML extras..."; \
      fi && \
      echo "✓ Dependencies installed"; \
    fi

# ===== Install ML extras if needed =====
RUN if [ "$EXTRAS" = "ml" ]; then \
      if [ -f "/app/.using-venv" ] && python -c "import sentence_transformers" 2>/dev/null; then \
        echo "✓ ML dependencies already present in .venv"; \
      else \
        echo "==> Installing ML dependencies..." && \
        pip install --prefer-binary \
          --extra-index-url https://download.pytorch.org/whl/cpu \
          ".[ml]" && \
        echo "✓ ML dependencies installed"; \
      fi; \
    fi

# Ensure PATH includes .venv if it exists (for CMD)
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]