# shreckLLM Config Simplification Proposal (Minimum-Decision Admin UX)

## Problem
Current admin-facing config exposes too many low-level runtime controls (queueing, batching, concurrency, timeouts, provider internals). This creates decision fatigue and increases misconfiguration risk.

Goal: make normal admin operation require only a few decisions, while preserving expert overrides for operators.

## Design Principle
1. Expose policy-level choices to admins.
2. Hide mechanism-level knobs behind presets.
3. Keep an expert mode for advanced operators.
4. Make safe defaults the path of least resistance.

## Recommended Admin Model

### Tier 1: Basic Mode (default UI)
Show only these 6 decisions:
1. `llm_enabled` (boolean)
2. `quality_profile` (enum: `economy`, `balanced`, `quality`)
3. `provider_strategy` (enum: `local_first`, `cloud_first`, `cloud_only`)
4. `max_monthly_budget_usd` (number, optional)
5. `data_retention` (enum: `short`, `standard`, `extended`)
6. `traffic_level` (enum: `low`, `medium`, `high`)

Everything else is derived automatically.

### Tier 2: Advanced Mode (collapsed/guarded)
Expose current low-level fields only behind an explicit “Advanced” toggle with warnings and reset-to-profile support.

## Derived Mapping (How basic settings configure internals)

### `quality_profile`
- `economy`: cheapest/fastest models; lower timeouts; smaller batching.
- `balanced`: current defaults.
- `quality`: stronger models; larger timeouts; lower concurrency to protect reliability.

Maps to:
- `provider_defaults`
- model selections consumed by shrecknet (`model_*`)
- request timeout defaults

### `provider_strategy`
- `local_first`: prioritize `ollama`, fallback to cloud providers.
- `cloud_first`: prioritize OpenAI/Anthropic, fallback to local.
- `cloud_only`: disable local provider routing.

Maps to:
- provider ordering and defaults
- `provider_limits` seed values

### `data_retention`
- `short` -> `memory_ttl_seconds=900`, `memory_max_messages=12`
- `standard` -> `memory_ttl_seconds=3600`, `memory_max_messages=24`
- `extended` -> `memory_ttl_seconds=14400`, `memory_max_messages=50`

### `traffic_level`
- `low` -> `max_concurrent_requests=4`
- `medium` -> `max_concurrent_requests=8`
- `high` -> `max_concurrent_requests=16`

Also auto-adjust:
- `max_queue_wait_seconds`
- provider queue caps

## Schema Proposal (v3)

## Basic groups returned by `GET /config/schema?mode=basic`

### General
- `id`: `general`
- `property`: `runtime`
- `fields`: `llm_enabled`, `quality_profile`, `provider_strategy`

### Capacity
- `id`: `capacity`
- `property`: `runtime`
- `fields`: `traffic_level`, `max_monthly_budget_usd`

### Data
- `id`: `data`
- `property`: `runtime`
- `fields`: `data_retention`

### Credentials
- `id`: `credentials`
- `property`: `runtime`
- `fields`: `openai_token_configured`, `anthropic_token_configured`, `ollama_cloud_token_configured`
- Note: still managed via dedicated token endpoints only.

## Advanced groups via `GET /config/schema?mode=advanced`
- Existing `providers`, `memory`, `concurrency` groups unchanged.
- Add `frontend_editable=false` for fields that should remain operator-only.

## Metadata Changes
Add two optional metadata keys for better UI behavior:
- `ui_mode`: `basic` | `advanced` (field visibility intent)
- `derived_from`: list of basic fields that control this value

Example:
- `memory_ttl_seconds`: `ui_mode=advanced`, `derived_from=["data_retention"]`

## API Behavior

### Read
- `GET /config` returns full resolved config (as today).
- `GET /config/basic` returns only basic fields.

### Write
- `PUT /config/basic` accepts only basic fields and recomputes derived internals.
- `PUT /config` remains for expert/admin advanced edits.

### Safety
- If advanced values diverge from profile-derived values, return flag:
  - `profile_overridden=true`
- UI can show: “Custom advanced overrides active”.

## Migration Plan
1. Introduce basic profile fields and derivation logic server-side.
2. Keep current fields for backward compatibility.
3. Update frontend to default to basic mode.
4. Add advanced drawer with clear warning and reset action.
5. After validation period, lock the noisiest fields (`frontend_editable=false`) for non-expert roles.

## What Admin Actually Decides After This Change
- Is AI on/off?
- Speed/cost vs quality profile?
- Local vs cloud routing preference?
- Expected traffic level?
- Retention posture?
- Optional budget ceiling?

This reduces decision count from dozens of technical knobs to 5-6 policy choices.
