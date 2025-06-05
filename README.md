# Shrecknet

## Background task worker

Cross-link generation is now handled by a Celery worker. To run the worker:

```bash
cd backend
celery -A app.task_queue.celery_app worker --loglevel=info
```

Ensure a Redis instance is available and configure `CELERY_BROKER_URL` if needed.
