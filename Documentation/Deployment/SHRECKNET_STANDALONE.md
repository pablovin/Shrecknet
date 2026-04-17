# Shrecknet Standalone Deployment

`shrecknet` now runs config-store-first.

## First Boot

- Start the stack with `docker compose -f docker-compose.shrecknet.yml up --build`.
- On first start, the app creates its SQLite databases and `configs.db` under `SHRECKNET_DATA_DIR`.
- No demo users, worlds, agents, jobs, media, or ontology instances are seeded automatically.

## First Admin Bootstrap

- Check bootstrap state with `GET /users/bootstrap`.
- When `has_users` is `false`, the first successful `POST /users/` registration is promoted to `ADMIN`.
- After the first user exists, normal role rules apply.

## Config Ownership

- Docker env is now limited to bootstrap/infrastructure concerns:
  data directory, media root, Neo4j reachability, Celery broker/backend, and optional JWT key material.
- Operational settings should be managed through the config store via `/config`.
- On first boot, missing config values are seeded from defaults and any bootstrap env values.
- After that, config-store values are authoritative for normal runtime behavior. Bootstrap env remains authoritative only for:
  `database_url`, `jobs_database_url`, `jwt_private_key_pem`, `jwt_public_key_pem`.

## Worker Config Reload

- API and worker processes both read from the same config store.
- Celery task startup reloads settings before execution, so changes made through `/config` are picked up by new tasks without code changes.
- Long-lived resources that depend on config, such as DB engines and embedding model selection, are recreated when their effective config changes.
