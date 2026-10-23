# Authenticated capabilities

`GET /config/capabilities` requires a valid authenticated Shrecknet user of any role.
It returns only `{ "enable_ai_agents": true }` (or `false`), using the current
server setting. Missing or invalid authentication is rejected by the standard
user dependency. No administrative configuration or secrets are returned.

ShreckOS uses this flag to show its ShreckWorld research companion. The complete
`GET /config` and `GET /config/` endpoints remain administrator-only. The flag
controls presentation; service endpoints continue to enforce their own policies.

Deploy Shrecknet before a frontend that consumes this additive endpoint. No data
migration is required. An older backend makes the frontend hide the companion
without blocking reading. Roll back both consumers and provider together if
restoring the previous administrative configuration contract.
