# Agents and Types

Use `python_sdk/examples/06_agents_management/01_agents_management_basics.py`.

## Agent fields

- `name` required: display name.
- `job` required: job type returned by `/agents/jobs`.
- `active` required: must be true for runtime usage.
- `description` optional: purpose/context.
- `writing_style` optional: behavior/persona guidance.
- `avatar_url` optional.
- `ontology_ids` on create: ontologies to link.

## Common job types

- `elder`: conversational retrieval/orchestration.
- `architect`: structure and world modeling workflows.
- `librarian`: library/document intelligence workflows.
- `novelist`: drafting/narrative generation workflows.

CORS is deployment configuration and cannot be updated through the configuration
API or SDK. Retired `cors_*` updates return HTTP 400. Use the shared root `.env`;
see [Browser CORS](../../../Documentation/Deployment/BROWSER_CORS.md).

## Foundry setup configuration

The existing low-level `raw_request` method supports Foundry administration;
there is no dedicated Foundry SDK resource or shared content token. With an
administrator login, create an integration using:

```python
integration = await sdk.raw_request("POST", "/config/integrations/foundry", json={
    "name": "Campaign table",
    "allowed_world_ids": [world_id],
    "default_world_id": world_id,
})
```

Treat the returned `key` as a one-time setup secret. Read responses omit the raw
key. Update `default_world_id` through `PUT /config/integrations/foundry/{id}`;
omit it to preserve the value or send `None` to clear it. The default must exist
and belong to a nonempty allow-list, or the server returns `422`. See the
[canonical Foundry API contract](../../../Documentation/API/FOUNDRY_INTEGRATION_API.md).
Players still authenticate individually for content and private conversations.

Runnable example: [Foundry setup](../../examples/06_agents_management/02_foundry_setup.py).
