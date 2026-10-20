# Shared browser CORS configuration

## Contract

Shrecknet and ShreckWorld read browser CORS exclusively from the repository-root
`.env`. Compose mounts that file read-only at `/configs/browser.env` in both API
containers. It does not interpolate CORS settings into their `environment` blocks.
The `shreckconfig` package owns parsing and validation for both services.

```dotenv
CORS_ALLOW_ORIGINS=["https://shrecknet.club","https://foundry.shrecknet.club"]
CORS_ALLOW_ORIGIN_REGEX=
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=["GET","POST","PUT","PATCH","DELETE","OPTIONS","HEAD"]
CORS_ALLOW_HEADERS=["Authorization","Content-Type","Range","If-Range","X-Shrecknet-Integration-Key"]
CORS_MAX_AGE=3600
```

Lists, booleans, and numbers use JSON syntax. The optional regex is a plain string;
an empty value disables regex matching. Origins must be explicit HTTP(S) origins
with no trailing slash, path, credentials, query, or fragment. Wildcard origins
are rejected. Add local frontend origins explicitly when needed; the tracked
`shrecknet.env.example` includes local development origins. No localhost or
preview-host regex is silently added by the APIs.

The loader reads dotenv values without interpolation or environment merging.
`CORS_*`, `SHRECKNET_CORS_*`, and `SHRECKWORLD_CORS_*` process environment values
cannot override this file. CORS configuration is not stored in a database.
Shrecknet removes legacy `cors_*` config rows on settings load, excludes CORS from
`GET /config` and `/config/schema`, and rejects those retired keys in `PUT /config`
with HTTP 400. Legacy seed-file keys are ignored for compatibility; the canonical
seed contains none. Other settings retain their existing configuration behavior.

Missing files or missing origins deny cross-origin access. Malformed configured
values fail API startup. Compose refuses to create a directory if the host `.env`
is absent. File permission errors fail startup; the container user must be able to
read the file. Health endpoints and authentication are unchanged. CORS never
substitutes for bearer-token or cookie authorization.

Middleware loads the policy at API startup. Editing `.env` does not change an
already-running middleware instance; restart the API processes to apply changes.
Protected ShreckWorld reader responses still expose range/ETag headers. Shrecknet
media responses use the same file policy as its API middleware.

## Container rollout

For the first rollout, build the new shared package into both API images and
recreate the containers to install their file mounts:

```bash
docker compose up -d --build shrecknet_api shreckworld_api
```

CORS values belong only in `.env`, never in Compose configuration or runtime
Configuration Manager. Compose contains only the read-only mount. Both Dockerfiles
install `shreckconfig` through a local build context alongside `shreckdoc`.
The Shrecknet image is also used by workers; its normal rebuild/redeployment
procedure still applies when updating application code.

For subsequent policy edits, restart the APIs. If an editor replaces `.env`
atomically, recreate the containers so their file bind mounts reference the new
file rather than its old inode:

```bash
docker compose up -d --force-recreate shrecknet_api shreckworld_api
```

There is no database schema migration or source reindexing. Legacy CORS rows are
deleted automatically; record any desired old allowlist in `.env` before rollout.
Rollback to older images requires restoring their legacy CORS configuration,
because old images do not understand the new file-only contract.

## Local development and tests

Install `shreckconfig` along with each service's existing local dependencies:

```bash
python -m pip install -e ./shreckconfig -e ./shreckdoc -e './shrecknet[test]'
```

Outside containers, the loader finds `.env` at the nearest ancestor containing
`docker-compose.yml`. It does not read other dotenv files. Tests can pass an
explicit temporary path to `load_cors_policy` without modifying deployment files.

## ShreckRPG and Chronicles

ShreckRPG is deployed from a separate source repository and is not part of this
Compose stack. Its CORS behavior has not been changed here. To adopt the same
contract, install `shreckconfig`, mount this same `.env` read-only at
`/configs/browser.env`, and initialize its middleware with:

```python
from shreckconfig import load_cors_policy

policy = load_cors_policy()
app.add_middleware(CORSMiddleware, **policy.model_dump())
```

Retain any service-specific exposed response headers. Remove its previous CORS
configuration sources when adopting this loader. Until then, Chronicles requires
its existing ShreckRPG CORS setup.
