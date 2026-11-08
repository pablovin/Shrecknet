# Companion Agent model configuration

Companion Agent is a separate configuration group from CharacterAgent embodiment
and the existing personal companion orchestrator. All six roles use the existing
`LLMModelTarget` object (`provider`, `name`, `reasoning`, nullable `reasoning_effort`, `web_search_enabled` default false).
Names come from the configured provider catalog; these are role assignments rather
than new provider implementations.

| Setting | Purpose | Required |
| --- | --- | --- |
| `model_companion_routing` | Ontology inference, v2 source planning and ontology-update routing | Required in v2; optional in legacy |
| `model_companion_web_search` | Dedicated v2 web evidence search | When model web backend selected |
| `model_companion_evidence` | V2 coverage review and factual claim verification | Required in v2 |
| `model_companion_synthesis` | Embodied streaming answer and optional image interpretation | For conversation |
| `model_companion_interim` | Provisional reaction during retrieval | When interim enabled |
| `model_companion_rapport_update` | Async completed-exchange rapport proposal | For feature enablement |

All six roles default to provider `openrouter`, model
`deepseek/deepseek-v4.1-flash`, with reasoning disabled and null effort. The dedicated web target defaults to
`web_search_enabled=true`; other targets default false. The model
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
model selector. `GET /llm_status/` exposes service state and the six model targets;
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

## Evidence v2 settings

The existing frontend `companion_agent` group exposes all six role selectors and
these controls through `/config/`, `/config/schema`, `/llm_status/` and user-safe
capabilities. Existing explicit saved settings, including blanks, are preserved.
Missing roles receive defaults; no gateway credentials are copied into role config.

| Setting | Default | Meaning |
| --- | --- | --- |
| `companion_query_pipeline_version` | `evidence_v2` | Automatic for new/missing settings; explicit saved `legacy` remains respected |
| `companion_general_knowledge_policy` | `disabled` | `labeled_opt_in` additionally requires per-turn opt-in |
| `companion_web_backend` | `model` | Dedicated model or explicitly configured `json` backend |
| `companion_routing_timeout_s` | 3 | Deadline for each ontology/planner call |
| `companion_evidence_timeout_s` | 8 | Deadline for each coverage/verifier call |
| `companion_web_timeout_s` | 12 | Web call deadline, bounded by overall retrieval/turn budget |
| `companion_recovery_max_rounds` | 1 | 0..1 targeted Companion recovery rounds |
| `companion_recovery_max_calls` | 2 | 1..4 calls per recovery round |
| `companion_interim_max_messages` | 2 | 0..3 permanent messages including immediate acknowledgement |
| `companion_interim_min_interval_s` | 8 | Minimum spacing for generated skits |
| `companion_interim_output_tokens` | 100 | 32..300 generated commentary tokens |

V2 requires valid routing and evidence targets when Companion is enabled; routing
cannot be disabled by the legacy optional-routing flag. Stage deadlines must fit
within the turn deadline. Enabled model web search requires an OpenRouter target; disabling its search flag
makes the tool unavailable. Gateway capability checks still apply at execution. Selecting
`json` requires the existing external URL/enable settings. Backend failures do not
silently switch providers. The old synthesis search flag controls only legacy
retrieval; v2 uses `model_companion_web_search` exclusively. Search is disabled for
all other v2 calls. Source-specific pipelines keep their existing model owners.

The seed enables optional generated commentary for new installations; the Settings
fallback remains false and persisted flags are preserved. Immediate v2
acknowledgement depends on the message maximum, not the generated-skit switch.
Set the maximum to zero to keep only status. Reload/restart API and workers
together after changing their cached runtime configuration.

## Controls and defaults

| Setting | Default | Meaning |
| --- | --- | --- |
| `companion_enabled` | false | Admission requires this and `enable_ai_agents` |
| `companion_model_routing_enabled` | false | Optional routing model |
| `companion_interim_enabled` | false | Optional concurrent reaction |
| `companion_tool_timeout_s` | 4 | Per-tool deadline, 0.1..60 seconds |
| `companion_retrieval_timeout_s` | 20 | Overall retrieval deadline, 0.1..120 seconds |
| `companion_turn_timeout_s` | 180 | Worker execution deadline, 10..1800 seconds |
| `companion_event_retention_days` | 7 | Event replay retention, 1..90 days |
| `companion_rapport_max_attempts` | 3 | Evaluation retries before audited no-op, 1..10 |
| `companion_max_evidence_tokens` | 10000 | Estimated evidence tokens, 100..32000 |
| `companion_output_tokens` | 2000 | Output tokens, 128..16000 |
| `companion_image_models` | [`openrouter/deepseek/deepseek-v4.1-flash`] | Explicit image-capable `provider/name` targets |
| `companion_shreckworld_url` | empty | Trusted ShreckWorld internal origin |
| `companion_chronicles_url` | empty | Trusted RPG internal origin |
| `companion_web_enabled` | false | Enable the legacy JSON search integration |
| `companion_web_search_url` | empty | Configured JSON search provider endpoint |
| `companion_web_search_token` | empty | Private provider token; redacted on reads |

For the legacy pipeline, enable `web_search_enabled` on `model_companion_synthesis` to use OpenRouter
search through the existing gateway credentials. Turn-level web permission still
applies; provider citations with excerpts enter the normal evidence validator.
Search is disabled on routing, interim, image interpretation, final synthesis and
rapport calls regardless of those roles' saved search flags. See
[shared agent web search](../../Architecture/LLM_WEB_SEARCH.md) for capabilities,
budgets, errors, compatibility, and deployment.

The legacy JSON web provider receives a POST search request and returns bounded
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

## Rich presentation controls

The existing Administration **Shrecknet → Companion Agent** schema group also
contains `companion_rich_response_enabled` (default true for new/missing settings; saved false values are preserved),
`companion_rich_response_timeout_s` (default 2, allowed 0.1–10), and
`companion_web_images_enabled` (default false). Saved values are preserved during
upgrade. Rich presentation requires `evidence_v2`; legacy replies remain text.
The optional timeout shares discovery/final-asset checking time inside the total
turn deadline. Failed media/composition does not invalidate verified prose.

Composition is deterministic and adds no model role. The existing
`model_companion_evidence` verifies narrative and optional block facts in the same
batched call. The synthesis and evidence prompts receive no independent search
permission from these controls. Web image discovery requires the JSON provider's
source-associated image contract and normal `tools.web` permission; leave it off
for providers without image support. Provider-managed model search continues to
return factual excerpts only. See [rich responses](Companion.md#verified-rich-responses).

After changing presentation flags/deadlines, refresh the API and turn workers using
the installation's normal configuration reload/restart procedure. Workers may
cache settings per process; each turn uses its entry-time settings snapshot.
