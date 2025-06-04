# Shrecknet

This project contains a Next.js frontend and a FastAPI backend.

## Persisting uploaded files

When running the system with Docker Compose, make sure the `public` directory from the frontend is mounted so uploaded images and documents are kept between container restarts.

```
docker compose up --build
```

Docker Compose now maps `./frontend/public` on your host to `/app/public` inside the frontend container. Any files uploaded through the API will appear in that directory on the host and will be preserved.
