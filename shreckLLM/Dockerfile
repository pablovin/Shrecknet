# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 120 --retries 10 .

EXPOSE 8110

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
