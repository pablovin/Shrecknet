FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/appuser \
    TORCHINDUCTOR_CACHE_DIR=/home/appuser/.cache/torchinductor

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      tesseract-ocr \
    && groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser \
    && mkdir -p /home/appuser/.cache/torchinductor \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN python -m pip install --upgrade "pip>=24.3.1" setuptools wheel

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN pip install --prefer-binary --index-url "$TORCH_INDEX_URL" "torch>=2,<3"

COPY app ./app
COPY default ./default

RUN pip install --no-cache-dir -e . \
    && chown -R appuser:appuser /app /home/appuser

USER appuser

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
