# Shared agent web-search configuration

Every agent model target accepts `web_search_enabled: bool`, default `false`,
independently of `reasoning` and `reasoning_effort`. This includes Shrecknet agent
roles and the independently stored ShreckWorld synthesizer target. Administrator
configuration writes replace a supplied target object; omitted target fields take
their defaults. Omitted top-level roles retain their saved configuration.

```json
{
  "model_elder_synthesis": {
    "provider": "openrouter",
    "name": "deepseek/deepseek-v4.1-flash",
    "reasoning": false,
    "reasoning_effort": null,
    "web_search_enabled": true
  }
}
```

## API and capability contract

- Shrecknet administrator `GET /config/`, `PUT /config/`, and `GET /config/schema`
  expose the option on every `llm_target`. `GET /llm_status/` includes the flag in
  model-target status. Existing authentication and administrator permissions apply.
- ShreckWorld administrator `GET`/`PUT
  /admin/settings/model-targets/shreckworld_synthesizer` expose the same option.
- shreckLLM `POST /chat`, `/chat/jobs`, and `/chat/stream` accept
  `web_search_enabled`, default `false`. All Shrecknet/ShreckWorld model-target
  callers forward the flag through their existing client.
- Gateway `GET /models` and `GET /providers/{provider_id}/models` return
  `model_capabilities: {"model-id": {"web_search": true|false|null}}` inside each
  provider catalog. `true` means implemented support confirmed by catalog data,
  `false` means unavailable, and `null` means unknown. Provider-level capabilities
  indicate the implemented adapter, not guaranteed support for every model.
- OpenRouter models advertising `tools` in `supported_parameters` are eligible.
  Other providers currently report `false`; missing OpenRouter metadata reports
  `null`. Unknown support is not silently treated as supported.
- Enabled unsupported requests fail with `web_search_unsupported` before model
  execution. `/chat` and `/chat/stream` return HTTP 400; asynchronous jobs report
  failure in their existing job status. Discovery outages remain service failures.

ShreckOS shows a shared Web search switch beside reasoning in every agent model
form, including ShreckWorld. The gateway catalog controls eligibility. Unsupported
or unknown choices cannot enable search; an existing enabled value can always be
turned off. Changing provider/model clears the flag so permission is not carried
silently to another target. Older catalog responses leave the control unavailable.

## Provider execution and sources

shreckLLM owns provider translation. OpenRouter requests use the server tool
`tools: [{"type":"openrouter:web_search", "parameters": ...}]`. The adapter pins
`engine: "exa"`, `max_uses: 1`, `max_results: 6`, `max_total_results: 6`,
`max_characters: 4000`, and top-level `max_tool_calls: 1` to bound each provider
attempt. OpenRouter executes the tool internally; the model decides whether to
search. Existing gateway retries or hedges can make additional provider attempts,
so these are per-attempt bounds, not a total job spending limit. Search adds
provider charges and latency, using the existing OpenRouter credentials.

Normal responses/job results preserve bounded provider `annotations` URL citations
separately from generated text. Streaming emits `annotations` events alongside
existing delta/usage/finish/done events. Each citation retains only URL, title,
and optional source content. Consumers that need grounding must process sources;
turning the switch on does not automatically extend an agent's domain-specific
citation validator or give web content canonical authority.

The integration follows the [OpenRouter web-search server tool](https://openrouter.ai/tool/web-search/)
and its [parameter contract](https://github.com/OpenRouterTeam/docs/blob/main/guides/features/server-tools/web-search.mdx).
No third-party search key or separately configured search-service URL is required.

## Companion permission and evidence

For Companion, enabling search on `model_companion_synthesis` permits provider web
retrieval. A turn must also allow it through `tools.web`; `disabled` prevents web
retrieval. Routing chooses whether an allowed turn needs search. The separate
retrieval call receives only the bounded query, not the identity, rapport, chat
history, private attachment bytes, or canonical source bundle. Provider URL
annotations with actual source excerpts become `external_web` evidence; generated
summaries, citations without excerpts, and unsafe/private URLs are excluded.
Embodied synthesis and image interpretation explicitly disable provider search so
search cannot bypass the turn permission or evidence/reference validator.

The original JSON search integration remains compatible: when provider search is
not enabled, `companion_web_enabled` plus `companion_web_search_url` use the existing
`{results:[{title,url,snippet}]}` protocol. OpenRouter search takes precedence when
both are configured. Other role flags remain independent, but a turn-level denial also disables
provider search for routing, interim, and the subsequent rapport evaluation. A search timeout or
failure follows the existing independent retrieval diagnostic path; configure
Companion's tool/overall retrieval deadlines for expected search latency.

ShreckWorld's existing world-level `web_search_enabled`/`web_search_policy` are
separate domain settings; this model-target flag controls the gateway request.
It does not implement a new world-level fallback-search workflow.

## Deployment and rollback

Deploy the gateway, Shrecknet API/workers, ShreckWorld API/workers, SDK, and ShreckOS
together. Existing configurations default search off; no SQL/graph schema migration,
re-embedding, new credential, or automatic enabling is required. Config normalization
preserves saved flags, including explicit blanks. Processes with cached settings
must reload/restart to observe updated targets. Validate provider availability and
account search permissions before enabling. To roll back, first turn the flag off
and keep it off when using older gateways that may ignore unknown request fields.


## Verification

Regression coverage checks every Shrecknet model role's independent saved flag,
legacy defaults/normalization, SDK updates, shared-client forwarding and citation
preservation, World target persistence/forwarding, catalog support, unsupported
normal/streaming requests, provider request budgets, bounded annotations, Companion
source normalization, and turn-level permission fences.

Verified package checks:

- Shrecknet focused Companion/config/status/client tests: 94 passed, excluding the
  existing `test_chat_polling_has_no_caller_generation_deadline` mismatch (expected
  no caller deadline, current implementation supplies 60 seconds).
- shreckLLM: 110 passed excluding two existing provider-update tests. The full run
  still fails `test_put_provider_rejects_empty_models_before_persisting` and
  `test_put_provider_rejects_unknown_model_before_persisting`; current provider
  bootstrap/update behavior accepts these writes.
- ShreckWorld: all 126 package tests passed.
- Python SDK: 51 passed.
- ShreckOS: 292 unit tests passed, lint passed with 125 existing warnings, production
  build passed with chunk-size warnings. Three focused administration journeys
  passed in Chromium desktop, mobile Chromium, and Firefox (9 browser cases).
- Public OpenRouter catalog discovery confirms `deepseek/deepseek-v4.1-flash`
  advertises `tools`. No paid live web-search request or deployment was performed.

Run Python checks from each package directory with `../.venv/bin/python -m pytest`.
For the Shrecknet focused run use `tests/test_character_companion.py`,
`tests/test_character_agent_model_config.py`, `tests/test_llm_status_operational.py`,
and `tests/test_shreckllm_client_provider_preflight.py`, with `-o addopts=''` and
`-k 'not test_chat_polling_has_no_caller_generation_deadline'`. The broader Shrecknet
suite retains its previously recorded legacy collection blockers. Browser checks
use `SHRECKOS_E2E_PORT=5194 npx playwright test tests/e2e/admin.spec.ts
--project=chromium --project=mobile --project=firefox -g 'shared agent model|web search|synthesizer'`.
WebKit was not rerun; its host-library limitation is recorded in Companion validation.
