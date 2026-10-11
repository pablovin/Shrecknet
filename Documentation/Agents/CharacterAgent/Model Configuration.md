# CharacterAgent model configuration

CharacterAgent has four independently configurable model roles. Each target is an
object with `provider`, `name`, and `reasoning` (boolean, default `false`). Roles
may share a provider/model or use different ones; changing one does not change the
others. Reasoning is forwarded to shreckLLM, which requests provider-native
reasoning when supported. It does not change character Steadiness or temperature.

## Stage ownership

| Configuration key | Calls using the target |
| --- | --- |
| `model_character_agent_character_incorporation` | Stage 0: initial identity description when missing, and final identity-summary refresh. |
| `model_character_agent_scene_interpretation` | Stage 1: subjective scene perspectives, including compact truncation recovery. Stage 4: source-level aspect/goal consolidation when events exist. |
| `model_character_agent_psychological_interpretation` | Stage 2: emotions, beliefs, and aspect/goal candidate events. Stage 3: directional trait interpretation. |
| `model_character_agent_decision_making` | One decision-making call in both character and neutral modes. |

Stages 2 and 3 run in parallel after Stage 1 validates. Embodiment replacement
attempts use the same target and reasoning flag as their original stage;
psychological truncation splits continue using the psychological target. Normal
call counts and trait/Steadiness scoring mathematics are unchanged. Scoring is
deterministic Python code. Draft generation, scene append processing, and offline
evaluation use the same routing.

`model_agents_repair_json` remains a separate shared service setting: a malformed
decision result can receive one repair call through it. It is actively used and
is not one of the four CharacterAgent roles. Embodiment does not use this repair
model. Legacy usage tags such as `character_agent.embodiment.character_incorporation`
still identify scene-perspective calls; the stage mapping above determines the
model, not the historical tag name.

## Frontend and HTTP contract

All config reads, schema reads, updates, and reloads below require an authenticated
administrator:

- `GET /config/`: current targets, including individual reasoning flags.
- `GET /config/schema`: the `character_agent` group contains exactly these four
  `model_*` fields, alongside its other controls. Each has `type: "llm_target"`,
  stage-specific help, `frontend_editable: true`, `change_impact: "hot"`, and
  `value_fields.reasoning` metadata for an independent boolean toggle.
- `PUT /config/`: a partial top-level update. Each supplied target replaces its
  entire target object; send provider/name together with the desired reasoning flag.
- `POST /config/reload`: reloads the persisted config for the handling process.

`GET /llm_status/` requires an authenticated user and reports the four targets
under `model_targets`, including `provider`, `model`, `reasoning`, and provider/model
availability. `GET /llm_status/models` supplies the provider catalog for selectors.
There is no frontend application tracked in this repository; these endpoints are
the integration contract for the frontend's selectors and toggles.

Example update (replace provider/model names with configured provider targets):

```json
{
  "model_character_agent_character_incorporation": {
    "provider": "openrouter", "name": "your-identity-model", "reasoning": true
  },
  "model_character_agent_scene_interpretation": {
    "provider": "openrouter", "name": "your-scene-model", "reasoning": false
  },
  "model_character_agent_psychological_interpretation": {
    "provider": "openrouter", "name": "your-psychology-model", "reasoning": true
  },
  "model_character_agent_decision_making": {
    "provider": "openrouter", "name": "your-decision-model", "reasoning": true
  }
}
```

Malformed target objects or reasoning values return `400` before persistence.
Retired keys also return `400`; config writes do not silently accept them. Existing
administrator authorization failures retain their normal authentication/permission
responses. Defaults are empty provider/name and reasoning disabled. Blank targets
remain blank until configured or reconciled when enabling Agents; an explicit
reasoning flag on a blank target survives save/reload. Enabling Agents still uses
the existing provider validation and target reconciliation flow.

Updates affect subsequent jobs: Celery reloads settings before tasks, while an
in-flight job keeps its selected targets. Config edits do not require service
restarts after all processes run compatible code. Startup prewarming and readiness
include all four roles. Source/chunk checkpoint fingerprints include incorporation,
scene, and psychological targets with their reasoning flags, and prompt version
`character-embodiment-v43-four-model-roles` prevents old routing outputs being reused.

## Automatic migration and rollback

On settings load/reload, before default seeding, the config store atomically:

1. Preserves existing incorporation and scene targets.
2. Copies the saved scene target into
   `model_character_agent_psychological_interpretation` if the new key is absent.
3. Copies `model_character_agent_deliberation` into
   `model_character_agent_decision_making` if the new key is absent.
4. Deletes `model_character_agent_framing` and `model_character_agent_deliberation`.

Copies preserve provider, name, and reasoning. They are independent records,
not links: later scene changes do not change psychology. Explicit new keys win,
including intentionally blank targets. Missing source keys fall back to normal
first-boot defaults or configured seed values. Legacy string targets follow the
existing string-to-target normalization and model-name policy. Copied values are
validated before any model-role writes; a validation failure retains the original
rows and fails settings loading. Repeated loads are idempotent. Legacy seed files
also map deliberation to decision making, initialize psychology from scene when
absent, and drop framing; canonical seed keys take precedence.

The migration preserves named target choices, while the requested routing changes
which stages use them: Stage 0 now uses incorporation and Stage 1 uses scene.
It does not move the old scene target into incorporation or exchange the two
saved records. The unused framing value is discarded.

Before deploying, back up `shrecknet/databases/shrecknet_config.db` (or the config
database in the configured data directory). Drain old workers and deploy API and
all workers together; old code can otherwise reintroduce retired keys. Upgrade
frontend consumers to the four canonical keys in the same rollout. No agent graph
migration, portrait regeneration, evidence rebuild, or memory re-embedding is
required. Rolling back to old code requires restoring the config database backup
while processes are stopped, or explicitly remapping decision making to the old
deliberation key; restoring old code alone does not reverse config migration.

## Python SDK

`sdk.character_agents.get_model_config()` returns `CharacterAgentModelConfig`.
`sdk.character_agents.update_model_config(CharacterAgentModelConfigUpdate(...))`
sends only selected non-null roles to `PUT /config/`. Both require administrator
credentials. Each nested `LLMModelTarget` carries its own reasoning flag. See
[SDK usage](../../../python_sdk/docs/character_agents.md) and the runnable
`python_sdk/examples/10_character_agent/02_model_configuration.py` example.

## Related contracts

- [CharacterAgent pipeline](CharacterAgent.md)
- [Decision making](Query/Query.md)
- [CharacterAgent endpoints](CharacterAgent%20-%20Endpoints.md)
