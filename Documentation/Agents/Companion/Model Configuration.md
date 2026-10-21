# Companion Agent model configuration

Companion Agent is a separate configuration group from CharacterAgent embodiment
and the existing personal companion orchestrator. All four roles use the existing
`LLMModelTarget` object (`provider`, `name`, `reasoning`, nullable `reasoning_effort`).
Names come from the configured provider catalog; these are role assignments rather
than new provider implementations.

| Setting | Purpose | Required |
| --- | --- | --- |
| `model_companion_routing` | Ambiguous-request tool routing | When model routing enabled |
| `model_companion_synthesis` | Embodied streaming answer and optional image interpretation | For conversation |
| `model_companion_interim` | Provisional reaction during retrieval | When interim enabled |
| `model_companion_rapport_update` | Async completed-exchange rapport proposal | For feature enablement |

All four roles default to provider `openrouter`, model
`deepseek/deepseek-v4.1-flash`, with reasoning disabled and null effort. The model
ID is verified against the [OpenRouter catalog](https://openrouter.ai/deepseek/deepseek-v4.1-flash).
Routing and interim calls remain disabled until explicitly enabled. Existing role
assignments, including deliberate blanks, are preserved on reload; defaults apply
to absent settings. Existing model roles are not automatically copied. Each supplied target replaces that object; omitted top-level roles retain
their values. Reasoning settings remain independent for each role. Image support
requires an explicit `provider/name` entry in `companion_image_models`; unknown
provider capability does not automatically enable private image submission.

## Frontend and SDK discovery

Administrator `GET /config/`, `PUT /config/`, `GET /config/schema` and existing
reload behavior include these settings. The schema exposes the `companion_agent`
group, stage-specific help and `llm_target` metadata for the existing frontend
model selector. `GET /llm_status/` exposes service state and the four model targets;
`GET /llm_status/models` exposes the provider catalog with capability metadata.
`GET /users/me/character-companion/capabilities` exposes user-safe configured roles,
tool availability, image support and limits. Configured does not guarantee provider
health; role availability may be unknown. Secrets never belong in capability output.
The SDK offers `get_model_config` and `update_model_config` using the same config API.

Example partial administrator write (substitute configured IDs):

```json
{
  "model_companion_synthesis": {
    "provider": "provider-id", "name": "streaming-model",
    "reasoning": false, "reasoning_effort": null
  },
  "model_companion_rapport_update": {
    "provider": "provider-id", "name": "small-model",
    "reasoning": false, "reasoning_effort": null
  }
}
```

## Controls and defaults

| Setting | Default | Meaning |
| --- | --- | --- |
| `companion_enabled` | false | Admission requires this and `enable_ai_agents` |
| `companion_model_routing_enabled` | false | Optional routing model |
| `companion_interim_enabled` | false | Optional concurrent reaction |
| `companion_tool_timeout_s` | 4 | Per-tool deadline, 0.1..60 seconds |
| `companion_retrieval_timeout_s` | 8 | Overall retrieval deadline, 0.1..120 seconds |
| `companion_turn_timeout_s` | 180 | Worker execution deadline, 10..1800 seconds |
| `companion_event_retention_days` | 7 | Event replay retention, 1..90 days |
| `companion_rapport_max_attempts` | 3 | Evaluation retries before audited no-op, 1..10 |
| `companion_max_evidence_tokens` | 10000 | Estimated evidence tokens, 100..32000 |
| `companion_output_tokens` | 2000 | Output tokens, 128..16000 |
| `companion_image_models` | [`openrouter/deepseek/deepseek-v4.1-flash`] | Explicit image-capable `provider/name` targets |
| `companion_shreckworld_url` | empty | Trusted ShreckWorld internal origin |
| `companion_chronicles_url` | empty | Trusted RPG internal origin |
| `companion_web_enabled` | false | Permit explicitly requested web retrieval |
| `companion_web_search_url` | empty | Configured JSON search provider endpoint |
| `companion_web_search_token` | empty | Private provider token; redacted on reads |

The web provider receives a POST search request and returns bounded
`results:[{title,url,snippet}]`. Public HTTP(S) result URLs are checked; private,
local and non-HTTP(S) URLs are rejected. The backend does not fetch result pages.
Keep configured origins and credentials in private server configuration. Empty
secret updates retain the existing token. Do not expose configuration storage to
browser clients outside the administrator API.

Runtime config storage is authoritative after initial seeding. API validation
requires synthesis/rapport targets when enabling Companion and optional role
targets when enabling those switches. Global agent reconciliation does not silently
choose Companion targets. Jobs read current settings at entry; coordinate process
reload/restart during rollout because settings may be cached in each process.
A conversational result records the targets selected by its worker. It does not
freeze the later asynchronous rapport worker's model assignment.

Deploy compatible gateway/API/workers before enabling. Back up configuration and
the main SQL store. Disable new admission for rollback; preserve additive settings
and tables. No canonical identity regeneration or re-embedding is required. See
[operations and remaining acceptance gates](Companion.md).
