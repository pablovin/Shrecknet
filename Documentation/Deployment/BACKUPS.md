# Category snapshot backups

API (admin only), router `shrecknet/app/api/routers/backup_snapshots.py`:

- `GET /backups/categories` – categories with size, file count, availability and free disk space.
- `GET|POST /backups/snapshots` – list / create (`categories`, `incremental`, `label`); creation runs as Celery task `backup.create_snapshot`.
- `POST /backups/snapshots/{id}/restore` – per-category restore (`categories`, `safety_snapshot`, `keep_only_admin_in_rpg` default true), task `backup.restore_snapshot`.
- `GET|DELETE /backups/snapshots/{id}`, `POST .../download-link`, `POST /backups/snapshots/import` (`.tar`).

Bundles (each stored as `<bundle>/<part>/...`, restored atomically per part):

- `shrecknet_worlds` = `shrecknet_db` + `graph` (Neo4j logical dump) + `documents` + `images` + `companion`. Restored together so the graph never references missing data.
- `shreckworlds` = ShreckWorld database/files/PDFs + `embeddings` (Qdrant snapshot API). Worlds with no matching Shrecknet world stay usable; relink with `PATCH /shreckworlds/{id}` (`shrecknet_world_id`, 409 on conflict).
- `shreckrpg` – with `keep_only_admin_in_rpg`, the snapshot is copied to a staging dir and pruned (`rpg_prune.py`) to games owned by the restoring admin and that admin's data; any unknown table aborts the restore. It references Shrecknet user/ontology ids, so restore it with `shrecknet_worlds` on a new install.
- `shrecknet_config`, `shreckllm_config` (`SHRECKNET_BACKUP_SHRECKLLM_DATA_DIR`), `secrets` (sensitive, off by default).

ShreckWorld, ShreckRPG and ShreckLLM bundles are available only when their data is mounted (`SHRECKNET_BACKUP_SHRECKWORLD_DATA_DIR`, `..._MEDIA_DIR`, `SHRECKNET_BACKUP_SHRECKRPG_DATA_DIR`, `SHRECKNET_BACKUP_SHRECKLLM_DATA_DIR`).

Layout: `<SHRECKNET_BACKUP_ROOT>/snapshot_<ts>/{manifest.json, <category>/...}`. Files are copied directly (reflink when available); SQLite uses the online backup API; incremental snapshots hard-link files unchanged since the previous snapshot (copy fallback across filesystems). Restore verifies sizes/checksums, swaps files in atomically, and database-backed categories need a service restart. `SHRECKNET_BACKUP_DIR` (host path, default `./backups`) must be writable by uid 1000. `backup_retention_count` (0 = keep all) prunes old snapshots. Legacy `/backups/create|restore` tar.gz endpoints are unchanged.

When ShreckRPG runs from its own compose project (the frontend repo's `docker-compose.yml`), set `SHRECKNET_BACKUP_SHRECKRPG_HOST_DIR` in the core `.env` to its data directory (for example `<frontend>/apps/shreckrpg/databases`); it is mounted at `/backup-sources/shreckrpg/data` in the api and worker.

Progress: both tasks write `{backup_kind: "snapshot", operation, categories, phase, status}` into the job details on every update (`phase` is `category:<bundle id>` while a bundle runs). The admin page polls the jobs API and renders a progress panel from it. The Neo4j part streams `graph.json` (same layout as the legacy dump) and reports node/relationship counts. `shrecknet_permissions` chowns the backup folder to the container uid on startup.
