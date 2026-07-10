# shreckLLM Configuration

Use `python_sdk/examples/05_shreckllm_management/01_shreckllm_setup.py`.

## Goals

- confirm shreckLLM is reachable and ready
- inspect/update runtime config
- set default provider and model
- reload config safely

## Endpoints surfaced by SDK

- Shrecknet side: `/llm_status/`
- Read `shreckllm_operational` from `/llm_status/` when deciding whether LLM-backed
  features can run.
- shreckLLM side: `/health`, `/ready`, `/models`, `/status`, `/config`, `/config/reload`,
  `/providers/{provider_id}/test`
