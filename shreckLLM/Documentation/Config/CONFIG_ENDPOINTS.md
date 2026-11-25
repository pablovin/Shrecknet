# shreckLLM Config Endpoints

Base URL (docker host): `http://localhost:8111`

Auth for all config endpoints:
- `Authorization: Bearer <shrecknet_token>`
- Required role: `admin` or `world_builder`

## GET /config
Returns current runtime config with masked secrets.

Response example:
```json
{
  "provider_defaults": {
    "ollama": {
      "models": ["gemma3:4b"],
      "base_url": "http://host.docker.internal:11434",
      "api_key": null
    },
    "openai": {
      "models": ["gpt-5-nano"],
      "base_url": null,
      "api_key": "sk-...abcd"
    }
  },
  "memory_ttl_seconds": 3600,
  "memory_max_messages": 24,
  "max_concurrent_requests": 8,
  "request_timeout_seconds": 45.0,
  "max_queue_wait_seconds": 10.0,
  "provider_limits": {
    "ollama": {"max_concurrent": 1},
    "ollama_cloud": {"max_concurrent": 1},
    "openai": {"max_concurrent": 10},
    "anthropic": {"max_concurrent": 10},
    "deepinfra": {"max_concurrent": 10},
    "openrouter": {"max_concurrent": 10},
    "azure_ai": {"max_concurrent": 10}
  }
}
```

Every configured provider has a positive `max_concurrent` value. shreckLLM
always acquires that provider's semaphore before calling its backend. The
global `max_concurrent_requests` limit remains an additional upper bound.

## PUT /config
Updates runtime config in sqlite and applies immediately.

Request body (partial patch supported):
```json
{
  "provider_defaults": {
    "openai": {
      "models": ["gpt-5-nano"],
      "base_url": null,
      "api_key": "sk-..."
    }
  },
  "max_concurrent_requests": 12
}
```

### Authoritative LLM timeout

`request_timeout_seconds` is the single user-configurable generation timeout.
It applies to each provider attempt for every provider, including `ollama`,
`ollama_cloud`, `openai`, `anthropic`, `deepinfra`, `openrouter`, and `azure_ai`.
Updating it hot-rebuilds the provider adapters; no service restart is required.
New installations bootstrap at 600 seconds per attempt. The one-time startup
configuration migration `migration_companion_timeouts_v11_applied` raises shorter
saved `request_timeout_seconds` and `chat_job_default_hedge_timeout_seconds` values
to 600 seconds. It raises shorter non-null `timeout_seconds` on explicit
`companion.*` hedge routes without changing their provider/model, and preserves
unrelated route limits. The orphan watchdog becomes at least 900 seconds and at
least the longest configured attempt plus 60 seconds. Longer saved limits remain.
This shared generation timeout affects all callers, including Companion.
The migration's writes and marker are atomic in `shreckllm_config.db`; failed
writes roll back and concurrent startups serialize. After the first successful
migration, later API edits are preserved on reload/restart. Back up the config DB
and restart/reload all gateway processes to adopt the migration; no SQL schema
change is required. See the [coordinated rollout](../../../Documentation/Agents/Companion/Model%20Configuration.md#ten-minute-call-rollout).
Caller deadlines, queue budgets and the logical-job orphan watchdog still apply.
Provider-default migrations preserve saved execution, queue and hedge controls;
installing new provider metadata does not reset these values.

The frontend discovers this field through `GET /config/schema`, where it is
reported as `frontend_editable: true` and `change_impact: hot`.

Read the current value:

```http
GET /config
Authorization: Bearer <shrecknet_admin_or_world_builder_token>
```

Update only the timeout:

```http
PUT /config
Authorization: Bearer <shrecknet_admin_or_world_builder_token>
Content-Type: application/json

{
  "request_timeout_seconds": 600
}
```

The response is the current public runtime configuration and includes the
applied `request_timeout_seconds`.

`chat_job_max_retries` is the single retry policy for all providers and
models. It counts retries after the first attempt: `0` permits one total
attempt, while `1` permits at most two total attempts. It is also exposed as a
hot, frontend-editable field:

```http
PUT /config
Authorization: Bearer <shrecknet_admin_or_world_builder_token>
Content-Type: application/json

{
  "chat_job_max_retries": 1
}
```

There are no Qwen-specific or other model-specific retry caps.

Shrecknet clients submit `POST /chat/jobs` and poll until the job reaches a
terminal state. They do not impose a second generation deadline. The legacy
Shrecknet `shreckllm_request_timeout_s` setting remains an internal HTTP
transport timeout for individual control-plane requests; it does not end a
submitted chat job.

To change a provider limit from a frontend, preserve the other provider rows
returned by `GET /config` and submit the updated map:

```json
{
  "provider_limits": {
    "ollama": {"max_concurrent": 1},
    "ollama_cloud": {"max_concurrent": 1},
    "openai": {"max_concurrent": 6},
    "anthropic": {"max_concurrent": 10},
    "deepinfra": {"max_concurrent": 10},
    "openrouter": {"max_concurrent": 10},
    "azure_ai": {"max_concurrent": 10}
  }
}
```

`GET /config/schema` marks `provider_limits` as `frontend_editable: true`.
Values below `1` are rejected with HTTP `422`.

When an upstream provider returns HTTP `429`, shreckLLM uses its
`Retry-After` header as the cooldown duration. If the header is absent or
invalid, `provider_limits.<provider_id>.cooldown_seconds_on_429` is used, with
a default of 10 seconds. Cooldown waits happen after provider-slot acquisition
and do not consume a chat-job retry. A short safety margin and randomized
jitter spread resumed requests across time.

Frontends should normally use the provider-scoped endpoints instead of
replacing the complete map:

- `GET /config/providers/{provider_id}/limits`
- `PUT /config/providers/{provider_id}/limits`

PUT request:

```json
{"max_concurrent": 10}
```

Response:

```json
{
  "provider_id": "deepinfra",
  "max_concurrent": 10,
  "global_max_concurrent": 8,
  "effective_max_concurrent": 8
}
```

`effective_max_concurrent` makes the additional global ceiling explicit to the
frontend. Changes are hot-applied by rebuilding the provider semaphores.

## DeepInfra provider

DeepInfra is bootstrapped as provider id `deepinfra` with base URL
`https://api.deepinfra.com/v1/openai` and an empty model list. It is unavailable
until the following sequence completes:

1. `PUT /config/deepinfra-token` stores the key and validates it against the
   provider API. On initial setup, with no configured model, this does not
   activate DeepInfra. If a model was already configured (for example during
   key rotation), the endpoint runs the same catalog check and functional ping
   as the other cloud-provider token endpoints.
2. `GET /providers/deepinfra/validate` may be used to re-check key validity.
3. `GET /providers/deepinfra/models` returns the authenticated provider model
   catalog so the frontend can present valid choices.
4. `POST /config/providers/deepinfra/models` submits one model. shreckLLM first
   checks the model against the authenticated model catalog, then runs the
   provider functional test. Only a successful result marks it active.

Deleting the key with `DELETE /config/deepinfra-token` immediately marks the
provider inactive.

DeepInfra uses the shared runtime `request_timeout_seconds` value. It has no
provider-specific generation timeout.

## OpenRouter provider

OpenRouter is bootstrapped as provider id `openrouter` with base URL
`https://openrouter.ai/api/v1` and an empty model list. Its setup and activation
contract matches DeepInfra:

1. `PUT /config/openrouter-token` stores the API key and validates it against
   OpenRouter's authenticated model-list endpoint. Without a configured model,
   successful key validation does not activate the provider.
2. `GET /providers/openrouter/validate` re-checks the stored key.
3. `GET /providers/openrouter/models` returns the authenticated model catalog.
   The public aggregate `GET /models` also includes OpenRouter while it is
   inactive specifically because its first model has not been selected. This
   lets configuration frontends populate model discovery before activation.
4. `POST /config/providers/openrouter/models` validates and stores a model,
   then runs the provider functional test before activation.

`DELETE /config/openrouter-token` removes the key and immediately deactivates
the provider. OpenRouter uses the shared runtime `request_timeout_seconds`
value and has no provider-specific generation timeout.

## Azure AI provider

Azure AI is available as provider ID `azure_ai`. It uses API-key authentication
and requires a resource endpoint configured as the OpenAI v1 base URL, for
example `https://<resource>.services.ai.azure.com/openai/v1`. Azure OpenAI
resource endpoints at `https://<resource>.openai.azure.com/openai/v1` are also
supported. The key and endpoint are stored in the server-side runtime
configuration; `GET /config` masks the key.

Configure both values with the standard provider endpoint:

```http
PUT /config/providers/azure_ai
Authorization: Bearer <shrecknet_admin_or_world_builder_token>
Content-Type: application/json

{
  "base_url": "https://<resource>.services.ai.azure.com/openai/v1",
  "api_key": "<azure-resource-key>"
}
```

`GET /providers/azure_ai/models` lists the models available to the configured
resource. Add the selected Azure deployment name using
`POST /config/providers/azure_ai/models` with `{"model":"<deployment-name>"}`;
ShreckLLM validates that it is in the discovered catalog and runs a functional
chat check before activating the provider. The same GET endpoint can be called
again whenever deployments change. Remove a model with
`DELETE /config/providers/azure_ai/models`. Removing the key through
`PUT /config/providers/azure_ai` with `{"api_key":null}` deactivates Azure AI.

The provider uses the shared OpenAI-compatible chat-completions adapter,
including streaming and structured response formats. Model-specific feature
support remains Azure-controlled. Microsoft Entra ID is not part of this
provider configuration, and Azure AI does not support OpenRouter web search.
Azure AI uses the shared runtime `request_timeout_seconds` value.

Configured and catalog model IDs remain unchanged. At chat execution time,
shreckLLM sends OpenRouter `provider.sort: "latency"` and
`provider.allow_fallbacks: true`. This is a latency-oriented routing preference
based on provider metrics, not a fixed latency guarantee or capacity reservation.

## POST /config/reload
Reloads runtime config from sqlite and reapplies adapters.

Response:
```json
{
  "reloaded": true,
  "config": { "...": "same schema as GET /config" }
}
```
