# Foundry integration API

## Purpose

The Foundry module is a read-only Shrecknet client. It reuses existing
authentication, user, World, ontology, ontology-instance, search, and Narrative
Scene endpoints. This page covers only the credential endpoints added for GM setup.

## Integration keys

An integration key identifies a Foundry installation during setup. It does not
authenticate a player and cannot retrieve World content. Keys use the
`shreck_foundry_ft_` prefix, are stored only as a SHA-256 hash, and their raw
value is returned once when created.

`POST /config/integrations/foundry` requires a Shrecknet administrator.

```json
{"name":"Edinburgh Foundry","allowed_world_ids":["edinburgh"]}
```

`GET /config/integrations/foundry` returns metadata only, never a raw key.

## Configuration-manager endpoints

All endpoints below require a Shrecknet administrator bearer token.

| Endpoint | Result |
| --- | --- |
| `GET /config/integrations/foundry` | Lists key metadata without secrets. |
| `POST /config/integrations/foundry` | Creates a key and returns its raw value exactly once. |
| `PUT /config/integrations/foundry/{id}` | Renames the integration or changes its allowed World IDs. |
| `POST /config/integrations/foundry/{id}/rotate` | Invalidates the old key and returns a replacement once. |
| `POST /config/integrations/foundry/{id}/revoke` | Disables the integration key. |

## Foundry GM setup

Both endpoints use `X-Shrecknet-Integration-Key` and require no player bearer token.

| Endpoint | Result |
| --- | --- |
| `POST /integrations/foundry/validate` | Server identity and version after key validation. |
| `GET /integrations/foundry/worlds` | Worlds allowed by this key. Empty allow-list means all Worlds. |

An invalid or revoked key returns `401`. A Foundry GM enters the key only in **Game Settings → Configure Settings → Shrecknet Connection**. Once the selected World is enabled, the key-entry form is no longer exposed; the key is not stored in a Foundry World setting because that setting is visible to all connected clients.

The GM-only panel calls the existing unauthenticated `GET /health` endpoint when a connection is enabled, manually checked, and every five minutes while a GM is connected. It records the configured connection as `online` or `offline`. This status is operational feedback only; players authenticate with their own existing Shrecknet account.

## Player content requests

After setup, players use their own existing Shrecknet sessions and the module
calls existing `/auth/token`, `/users/me`, `/worlds/{world_id}`, `/ontologies`,
and `/ontology-instances` APIs. The configured World selects the content scope;
the module does not create a duplicate content API.

`GET /ontology-instances/scenes?ontology_id={id}&query={scene_name}` returns
lightweight Narrative Scene records for a scene collection. `query` matches scene
names case-insensitively; each result includes its source page and milestone and
perspective counts. The Foundry module calls it once per ontology in its selected
World and groups the results by source page. The module's navigation search calls
this endpoint alongside its existing page search and presents page hits before
Narrative Scene hits.

### Preloaded instance pages

`GET /ontology-instances/pages` accepts the same authenticated paging and
ontology/entity-definition filters as `/ontology-instances/basic`. It returns
complete entity content for every page in the requested collection, including
text, timestamps, image URLs, generated text, properties, and relationships.
Each page also includes `related_content` cards (`id`, `name`, `image`) and
Narrative Scene previews (`id`, `name`, `description`).

`POST /ontology-instances/resolve-entities` also returns `avatar_url` for each
resolved target entity. Foundry uses this optional field to render relationship
links as image cards; clients that do not use it remain compatible.

The Foundry module uses this endpoint when opening a collection and caches the
result. Opening one of those listed pages therefore makes no additional content
request; a scene's complete detail is requested only when the player opens it.

The module reads property definitions from
`GET /ontologies/{ontology_id}/entities/{entity_id}/properties` to label page
property values. Media URLs may be absolute or use `/media/...`; localhost media
URLs are normalized to the configured Shrecknet origin so a deployed Foundry
client does not attempt to load its own localhost.
## Operations

Configure CORS for the Foundry deployment origin and the
`X-Shrecknet-Integration-Key` header. Use HTTPS outside development.
