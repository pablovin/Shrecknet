# Providers Status and Readiness

Use `python_sdk/examples/05_shreckllm_management/02_provider_keys_models_status.py`.

## Key tasks

- set or clear OpenAI/Anthropic keys
- validate providers
- manage provider model lists
- run SDK preflight readiness report

## Readiness contract

`preflight_agents_llm_ready()` is ready when:
- shreckLLM is reachable from Shrecknet
- at least one provider is valid
- that provider has at least one available model

Use `strict=True` to raise `ConfigurationReadinessError` on failure.
