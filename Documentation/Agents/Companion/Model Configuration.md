# Companion Agent model configuration

Companion Agent is a separate configuration group from CharacterAgent embodiment
and the existing personal companion orchestrator. All six roles use the existing
`LLMModelTarget` object (`provider`, `name`, `reasoning`, nullable `reasoning_effort`, `web_search_enabled` default false).
Names come from the configured provider catalog; these are role assignments rather
than new provider implementations.

| Setting | Purpose | Required |
| --- | --- | --- |
| `model_companion_routing` | Scope/continuation decisions, simple research planning, memory relevance and public-query preparation | Required |
| `model_companion_web_search` | Dedicated v2 web evidence search | When no JSON search URL is configured |
| `model_companion_evidence` | V2 coverage review and factual claim verification | Required in v2 |
| `model_companion_synthesis` | Embodied streaming answer and optional image interpretation | For conversation |
| `model_companion_interim` | Provisional reaction during retrieval | When interim enabled |
| `model_companion_rapport_update` | Async completed-exchange rapport proposal | For feature enablement |

All six roles default to provider `openrouter`, model
`deepseek/deepseek-v4.1-flash`, with reasoning disabled and null effort. The dedicated web target defaults to
`web_search_enabled=true`; other targets default false. The model
ID is verified against the [OpenRouter catalog](https://openrouter.ai/deepseek/deepseek-v4.1-flash).
Routing is required when Companion is enabled; generated interim calls remain optional. Existing role
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
| `companion_routing_timeout_s` | 600 | Deadline for each scope/planner/relevance/public-query call |
| `companion_evidence_timeout_s` | 600 | Deadline for each coverage/verifier call |
| `companion_web_timeout_s` | 600 | Web call deadline, bounded by overall retrieval/turn budget |
| `companion_recovery_max_rounds` | 1 | 0 disables Web recovery; 1 permits one assessed Web batch |
| `companion_recovery_max_calls` | 2 | Saved range 1..4; Web execution uses at most min(value, 2) queries |
| `companion_interim_max_messages` | 2 | 0..3 permanent messages including immediate acknowledgement |
| `companion_interim_min_interval_s` | 8 | Minimum spacing for generated skits |
| `companion_interim_output_tokens` | 100 | 32..300 generated commentary tokens |

The evidence-first pipeline requires valid routing and evidence targets when
Companion is enabled. Retired pipeline/optional-routing flags do not select a
legacy runner and are not accepted by `/config/`. Stage deadlines must fit
within the turn deadline. Enabled model web search requires an OpenRouter target; disabling its search flag
makes the tool unavailable. Gateway capability checks still apply at execution. A configured `companion_web_search_url` selects the JSON adapter; otherwise the
dedicated `model_companion_web_search` target controls search availability. Backend
failures do not silently switch providers. Search is disabled for all other calls. Source-specific pipelines keep their existing model owners.

The seed enables optional generated commentary for new installations; the Settings
fallback remains false and persisted flags are preserved. Immediate v2
acknowledgement depends on the message maximum, not the generated-skit switch.
Set the maximum to zero to keep only status. Reload/restart API and workers
together after changing their cached runtime configuration.

## Controls and defaults

| Setting | Default | Meaning |
| --- | --- | --- |
| `companion_enabled` | false | Admission requires this and `enable_ai_agents` |
| `companion_interim_enabled` | false | Optional concurrent reaction |
| `companion_tool_timeout_s` | 600 | Per-source and synthesis deadline, 0.1..600 seconds |
| `companion_retrieval_timeout_s` | 615 | Per-round parallel retrieval window, 0.1..660 seconds |
| `companion_turn_timeout_s` | 10200 | Total sequential execution budget, 10..10800 seconds |
| `companion_event_retention_days` | 7 | Event replay retention, 1..90 days |
| `companion_rapport_max_attempts` | 3 | Evaluation retries before audited no-op, 1..10 |
| `companion_max_evidence_tokens` | 10000 | Model evidence projection tokens, 100..32000; full admitted records remain retained |
| `companion_output_tokens` | 2000 | Output tokens, 128..16000 |
| `companion_image_models` | [`openrouter/deepseek/deepseek-v4.1-flash`] | Explicit image-capable `provider/name` targets |
| `companion_shreckworld_url` | empty | Trusted ShreckWorld internal origin |
| `companion_chronicles_url` | empty | Trusted RPG internal origin |
| `companion_web_enabled` | false | Enable the legacy JSON search integration |
| `companion_web_search_url` | empty | Configured JSON search provider endpoint |
| `companion_web_search_token` | empty | Private provider token; redacted on reads |

For the legacy pipeline, enable `web_search_enabled` on `model_companion_synthesis` to use OpenRouter
search through the existing gateway credentials. Web recovery is available after a valid insufficient assessment
whenever a provider is configured; the legacy per-turn `tools.web` value is ignored.
Provider citations with excerpts enter the normal evidence validator.
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
source-associated image contract and a configured web provider; leave it off
for providers without image support. Provider-managed model search continues to
return factual excerpts only. See [rich responses](Companion.md#verified-rich-responses).

After changing presentation flags/deadlines, refresh the API and turn workers using
the installation's normal configuration reload/restart procedure. Workers may
cache settings per process; each turn uses its entry-time settings snapshot.

## Ten-minute call rollout

New/missing settings use 600-second per-call limits (routing, evidence review,
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
`4*routing + 4*evidence + 5*tool + rounds*retrieval + min(evidence,60) + 2*media + 60`
seconds, where `rounds=1+companion_recovery_max_rounds`. It reserves optional
scope/continuation work, research planning, optional memory relevance,
image interpretation, one assessment plus one identical-context retry,
both drafts/verifications, identity/source checks and orchestration overhead.
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

Assessment recovery adds at most one 60-second retry with the same evidence,
clipped to `companion_evidence_timeout_s` and remaining turn time. Primary
assessment and final verification each default to 600 seconds. The retry uses the
existing evidence model role, with no new setting, startup migration or source
recovery round. The default turn limit stays 10,200 seconds; saved limits are not
lowered by the reduced minimum-budget calculation. A valid insufficient assessment
can produce one Web batch of at most two queries and five total records. No
post-Web reassessment or separate public-query rendering call is made. Persistent
assessment failure skips Web and attempts grounded synthesis/verification with
limitations, then attributed extracts if necessary. Gateway jobs retain their
provider-attempt/watchdog lifetimes; the retry deadline bounds Companion waiting.
See the [recovery contract](./Companion.md#compact-evidence-assessment-and-extractive-recovery).
