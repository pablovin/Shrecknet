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

An invalid or revoked key returns `401`. The module must never store the key in a
Foundry World setting because that setting is visible to all connected clients.

## Player content requests

After setup, players use their own existing Shrecknet sessions and the module
calls existing `/auth/token`, `/users/me`, `/worlds/{world_id}`, `/ontologies`,
and `/ontology-instances` APIs. The configured World selects the content scope;
the module does not create a duplicate content API.

## Operations

Configure CORS for the Foundry deployment origin and the
`X-Shrecknet-Integration-Key` header. Use HTTPS outside development.
