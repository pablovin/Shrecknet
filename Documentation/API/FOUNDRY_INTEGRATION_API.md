# Foundry integration API

## Purpose

The Foundry module browses Atlas and ShreckWorld content and uses the existing
owner-authorized Chronicle and Character Companion contracts. It reuses existing
authentication, user, World, ontology, ontology-instance, search, and Narrative
Scene endpoints. This page covers only the credential endpoints added for GM setup.

## Integration keys

An integration key identifies a Foundry installation during setup. It does not
authenticate a player and cannot retrieve World content. Keys use the
`shreck_foundry_ft_` prefix, are stored only as a SHA-256 hash, and their raw
value is returned once when created.

`POST /config/integrations/foundry` requires a Shrecknet administrator.

```json
{"name":"Edinburgh Foundry","allowed_world_ids":["edinburgh"],"default_world_id":"edinburgh"}
```

`GET /config/integrations/foundry` returns metadata only, never a raw key.

## Configuration-manager endpoints

All endpoints below require a Shrecknet administrator bearer token.

| Endpoint | Result |
| --- | --- |
| `GET /config/integrations/foundry` | Lists key metadata without secrets. |
| `POST /config/integrations/foundry` | Creates a key and returns its raw value exactly once. |
| `PUT /config/integrations/foundry/{id}` | Renames the integration, changes its allowed World IDs, or sets/clears `default_world_id`. |
| `POST /config/integrations/foundry/{id}/rotate` | Invalidates the old key and returns a replacement once. |
| `POST /config/integrations/foundry/{id}/revoke` | Disables the integration key. |

### Default World

Create and update accept optional nullable `default_world_id` (1–64 characters).
The World must exist and, when `allowed_world_ids` is nonempty, belong to that
list; otherwise the API returns `422`. An omitted update preserves the current
default; explicit `null` clears it. Changing the allow-list cannot exclude a
retained default. Set a replacement or clear it in the same update.

Validation returns `default_world_id`; new module setup limits its choices to
that World and preselects it. If that World was deleted, setup shows no available
World rather than choosing another. Legacy keys with no default retain their
allowed-World picker; a single result is preselected. Existing saved Foundry
bindings are unchanged by admin edits until the GM reapplies setup. The key
still grants no content authorization; this binding is navigation configuration.

The normal API startup adds nullable `foundry_integrations.default_world_id` to
existing databases idempotently. Existing rows remain null. Roll back application
code without dropping this additive column; existing keys and bindings remain valid.

## Foundry GM setup

Both endpoints use `X-Shrecknet-Integration-Key` and require no player bearer token.

| Endpoint | Result |
| --- | --- |
| `POST /integrations/foundry/validate` | Server identity, version, and nullable `default_world_id` after key validation. |
| `GET /integrations/foundry/worlds` | Worlds allowed by this key. Empty allow-list means all Worlds. |

An invalid or revoked key returns `401`. A Foundry GM enters the key only in **Game Settings → Configure Settings → Shrecknet Connection**. The key is never stored in a Foundry World setting because that setting is visible to all connected clients. The GM can explicitly choose **Update World connection** to re-enter the key and apply a new binding; the prior saved connection remains until its replacement is saved. The key stays in window memory during the guided setup and is discarded on Save, Back, cancel, or close.

The five-minute GM monitor uses the existing unauthenticated Shrecknet `GET /health` endpoint to record the saved connection as `online` or `offline`. Manual wizard checks show local per-service results; the saved status changes on Save or a monitor check. This status is operational feedback only; players authenticate with their own existing Shrecknet account.

The wizard's **Continue · choose World →** action uses the credential endpoints
above. Once a World is selected, **Test all connections** revalidates the key and
selected World and sends unauthenticated `GET /health` requests to all three
configured APIs, requiring JSON `status: "ok"`. Each request has a ten-second
timeout. **Save connection** becomes available only after every check succeeds;
changed settings require another test. Rechecking an already saved connection
uses health requests only, without a setup key. The periodic monitor remains a
Shrecknet-only health check. Health checks do not authorize or test private user
content. See the [guided setup guide](../Deployment/FOUNDRY_MODULE.md#setup-checks-and-saving).

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
