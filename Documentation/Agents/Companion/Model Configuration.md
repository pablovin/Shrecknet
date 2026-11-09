# Companion Agent model configuration

Companion Agent is a separate configuration group from CharacterAgent embodiment
and the existing personal companion orchestrator. The four active roles and two compatibility roles use the existing
`LLMModelTarget` object (`provider`, `name`, `reasoning`, nullable `reasoning_effort`, `web_search_enabled` default false).
Names come from the configured provider catalog; these are role assignments rather
than new provider implementations.

| Setting | Purpose | Required |
| --- | --- | --- |
| `model_companion_routing` | Scope/continuation and search/chat/clarify routing | Required |
| `model_companion_web_search` | Router-selected Web evidence search | When no JSON search URL is configured |
| `model_companion_evidence` | Inactive compatibility role | No |
| `model_companion_synthesis` | Embodied Markdown answer and optional image interpretation | For conversation |
| `model_companion_interim` | Inactive compatibility role | No |
| `model_companion_rapport_update` | Async completed-exchange rapport proposal | For feature enablement |

All six roles default to provider `openrouter`, model
`deepseek/deepseek-v4.1-flash`, with reasoning disabled and null effort. The dedicated web target defaults to
`web_search_enabled=true`; other targets default false. The model
ID is verified against the [OpenRouter catalog](https://openrouter.ai/deepseek/deepseek-v4.1-flash).
Routing, synthesis and rapport targets are required when Companion is enabled;
generated interim and evidence-review calls have been removed. Existing role
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
| `companion_general_knowledge_policy` | `disabled` | `labeled_opt_in` additionally requires per-turn opt-in |
| `companion_routing_timeout_s` | 600 | Deadline for each scope/router call |
| `companion_evidence_timeout_s` | 600 | Deadline for optional image interpretation |
| `companion_web_timeout_s` | 600 | Web call deadline, bounded by overall retrieval/turn budget |
| `companion_recovery_max_rounds` | 1 | Inactive compatibility setting |
| `companion_recovery_max_calls` | 2 | Inactive compatibility setting; normal Web cap is two calls |
| `companion_interim_max_messages` | 2 | 0 disables acknowledgement; positive values emit just one |
| `companion_interim_min_interval_s` | 8 | Inactive compatibility setting |
| `companion_interim_output_tokens` | 100 | Inactive compatibility setting |

The evidence-first pipeline requires valid routing, synthesis and rapport targets when
Companion is enabled. Retired pipeline/optional-routing flags do not select a
legacy runner and are not accepted by `/config/`. Stage deadlines must fit
within the turn deadline. Enabled model web search requires an OpenRouter target; disabling its search flag
makes the tool unavailable. Gateway capability checks still apply at execution. A configured `companion_web_search_url` selects the JSON adapter; otherwise the
dedicated `model_companion_web_search` target controls search availability. Backend
failures do not silently switch providers. Search is disabled for all other calls. Source-specific pipelines keep their existing model owners.

Generated commentary switches/model keys remain accepted for saved configuration and
SDK compatibility but have no execution effect. One static acknowledgement depends
only on `companion_interim_max_messages`. Zero keeps status events only. Reload API
and workers together after cached runtime configuration changes.

## Controls and defaults

| Setting | Default | Meaning |
| --- | --- | --- |
| `companion_enabled` | false | Admission requires this and `enable_ai_agents` |
| `companion_interim_enabled` | false | Inactive compatibility setting |
| `companion_tool_timeout_s` | 600 | Per-source and synthesis deadline, 0.1..600 seconds |
| `companion_retrieval_timeout_s` | 615 | Per-round parallel retrieval window, 0.1..660 seconds |
| `companion_turn_timeout_s` | 10200 | Total sequential execution budget, 10..10800 seconds |
| `companion_event_retention_days` | 7 | Event replay retention, 1..90 days |
| `companion_rapport_max_attempts` | 3 | Evaluation retries before audited no-op, 1..10 |
| `companion_max_evidence_tokens` | 10000 | Model evidence projection tokens, 100..32000; full admitted records remain retained |
| `companion_output_tokens` | 8000 | Technical ceiling, 128..16000; prompt guidance controls ordinary length |
| `companion_image_models` | [`openrouter/deepseek/deepseek-v4.1-flash`] | Explicit image-capable `provider/name` targets |
| `shreckworld_base_url` | configured deployment origin | Trusted ShreckWorld internal origin |
| `shreckrpg_base_url` | configured deployment origin | Trusted RPG internal origin |
| `companion_web_search_url` | empty | Configured JSON search provider endpoint |
| `companion_web_search_token` | empty | Private provider token; redacted on reads |

Only `model_companion_web_search` enables model-managed Web search. Configured
Web is available to the router when `tools.allowed` permits it; the legacy
`tools.web` field does not gate selection. Queries are unrestricted by public/private
classification. There is no public-query catalog or filtering stage. Web runs with
selected internal tools in one bounded parallel round, never as assessment recovery.
Search is disabled on scope/routing, image interpretation, synthesis and rapport.
See [shared agent web search](../../Architecture/LLM_WEB_SEARCH.md).

The legacy JSON web provider receives a POST search request and returns bounded
`results:[{title,url,snippet}]`. Public HTTP(S) result URLs are checked; private,
local and non-HTTP(S) URLs are rejected. The backend does not fetch result pages.
Keep configured origins and credentials in private server configuration. Empty
secret updates retain the existing token. Do not expose configuration storage to
browser clients outside the administrator API.

Runtime config storage is authoritative after initial seeding. API validation
requires routing/synthesis/rapport targets when enabling Companion. Global agent reconciliation does not silently
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
upgrade. Rich presentation uses the sole Markdown pipeline; historical replies remain readable.
The optional timeout shares discovery/final-asset checking time inside the total
turn deadline. Failed media/composition does not invalidate cited Markdown.

Composition is deterministic and adds no model role or verifier call. It uses
resolved citations and retrieved metadata. Web image discovery requires the JSON
provider's source-associated image contract and configured opt-in; leave it off
for providers without image support. Provider-managed search supplies excerpts
only. See [rich responses](Companion.md#deterministic-rich-responses).

After changing presentation flags/deadlines, refresh the API and turn workers using
the installation's normal configuration reload/restart procedure. Workers may
cache settings per process; each turn uses its entry-time settings snapshot.

## Ten-minute call rollout

New/missing settings use 600-second per-call limits (routing, image interpretation,
retrieval and synthesis), a 615-second retrieval-round window, and a 10,200-second
turn budget. Fast calls return immediately. Startup now runs a one-time saved
configuration migration: shorter stored limits are raised to these values, while
longer limits are retained. Manual configuration updates are no longer required
for a normal upgrade. The target values are also available through `PUT /config/`
for inspection or later administrator changes:

```json
{
  "companion_tool_timeout_s": 600,
  "companion_web_timeout_s": 600,
  "companion_routing_timeout_s": 600,
  "companion_evidence_timeout_s": 600,
  "companion_retrieval_timeout_s": 615,
  "companion_turn_timeout_s": 10200
}
```

Timeout updates reject a retrieval window shorter than the local/web calls or a
turn budget shorter than the conservative serial execution path. The minimum is
`3*routing + evidence + 5*tool + retrieval + 2*media + 60` seconds. It reserves
scope/continuation, routing, optional image interpretation, initial/final identity,
source checks and two synthesis attempts. There is one parallel retrieval round.
After the migration, explicit administrator changes remain authoritative on later
reloads/restarts. Choosing shorter limits deliberately reduces the call budgets.

ShreckLLM's shared `request_timeout_seconds` and default hedge attempt timeout
now bootstrap at 600 seconds. A gateway configuration migration raises shorter
saved values to 600 seconds and preserves longer ones. Explicit hedge routes whose
usage tag starts with `companion.` are also raised to at least 600 seconds when
they have a shorter non-null `timeout_seconds`; their provider/model choices and
unrelated routes remain unchanged. The orphan watchdog is raised to at least
900 seconds and at least the longest configured primary/default/route attempt plus
60 seconds. Review queue/retry behavior under load. These gateway generation
settings affect other workloads using that shared service as well.
ShreckWorld's `SHRECKWORLD_SYNTHESIS_TIMEOUT_SECONDS` defaults to 600 in code and
Compose; explicit environment overrides take precedence. Deep retrieval remains
bounded by the parent Companion call even when child stages have longer budgets.

Drain active turns and restart API/Companion workers together after configuration
changes. Coordinate Redis visibility timeout across all workers sharing the
broker; Shrecknet configures at least `companion_turn_timeout_s+120` seconds.
Workers heartbeat every fifteen seconds; stale-outbox detection is ninety seconds,
independent of the maximum turn budget. External proxies and clients must allow
long-lived SSE; reconnect through the existing cursor/snapshot flow instead of
resubmitting turns. Provider errors, cancellation, expired authentication and
insufficient evidence can still prevent a supported answer.

The migrations update configuration data in each service's existing
`config_settings` table; they do not alter SQL schemas, graph data, models,
credentials, enable flags, evidence/output token budgets or source selections.
Shrecknet records `migration_companion_timeouts_v1_applied` in
`shrecknet_config.db`; the gateway records
`migration_companion_timeouts_v11_applied` in `shreckllm_config.db`. Each service
locks its configuration database, checks its marker, and commits its timeout
changes and marker atomically. Concurrent startups serialize this check. A failed
migration rolls back and is retried on the next load. The stores migrate
independently, so deploy/restart both services and every API/worker process.

Back up both configuration databases and drain active turns before deployment.
After restart, inspect Shrecknet `GET /config/` and gateway `GET /config` to verify
effective values. Existing cached processes do not acquire the new values until
reload/restart. ShreckWorld timeouts are environment-owned, not stored in these
databases: remove any shorter explicit `SHRECKWORLD_SYNTHESIS_TIMEOUT_SECONDS`
override separately if ten-minute deep calls are required.

No schema migration or re-embedding is required. To roll back, drain active work
and restore compatible code/configuration backups. To deliberately lower limits
while keeping the new code, update the configuration APIs after the migration;
retain the markers so restarts respect those edits. Never delete configuration
databases or migration markers to adjust budgets.

Truncated synthesis retries once with the same evidence and citation aliases and
shorter-answer guidance, bounded by the remaining turn budget. Persistent truncation
is a typed generation failure. No assessment, verifier, sufficiency or structured
repair calls remain. Saved turn/output settings are preserved; the new 8,000-token
ceiling applies to missing settings, not an automatic overwrite of custom limits.
