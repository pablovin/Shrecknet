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

# Copy metadata first for caching
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "pip<25.4"  # keep resolver stable but don't hit PyPI every time

# Copy minimal package structure so setuptools can resolve package
RUN mkdir -p app
COPY app/__init__.py app/__init__.py

# By default, build the API flavor (no ML). Override via --build-arg EXTRAS=ml
ARG EXTRAS=
# If you need system libs (rare), add them here. We DON'T install gcc/g++ by default.
# RUN --mount=type=cache,target=/var/cache/apt \
#     --mount=type=cache,target=/var/lib/apt \
#     apt-get update && apt-get install -y --no-install-recommends <libs> && rm -rf /var/lib/apt/lists/*

# Install deps (API only by default)
# NOTE: We delay full source COPY to keep cache hits when code changes.
RUN --mount=type=cache,target=/root/.cache/pip \
    sh -c 'if [ -z "$EXTRAS" ]; then pip install .[test]; else echo "Defer ML extras to runtime stage"; fi'

# Now copy the rest of the app
COPY . .

# If EXTRAS=ml, install the ML extras using the official CPU wheel index for torch
# This avoids huge GPU wheels / slow resolution.
RUN --mount=type=cache,target=/root/.cache/pip \
    sh -c 'if [ "$EXTRAS" = "ml" ]; then \
      pip install --prefer-binary \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        ".[ml]" ; \
    fi'

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]