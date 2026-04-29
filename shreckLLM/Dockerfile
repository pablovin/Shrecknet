FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN pip install --no-cache-dir .

EXPOSE 8110

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8110"]
