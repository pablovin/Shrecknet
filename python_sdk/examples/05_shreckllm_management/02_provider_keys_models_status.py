import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Manage provider keys/models and evaluate AI configuration readiness.
# Expected result:
# - Prints provider validation, model catalog status, and preflight verdict.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    shreckllm_base_url = os.getenv("SHRECKLLM_BASE_URL", "http://localhost:8111")

    async with Shrecknet(base_url=base_url, shreckllm_base_url=shreckllm_base_url) as sdk:
        await ensure_user_and_login(sdk)

        openai_key = os.getenv("OPENAI_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")

        # Optionally store keys when provided via environment.
        if openai_key:
            print("set_openai_key")
            await sdk.shreckllm.set_openai_key(openai_key)
        if anthropic_key:
            print("set_anthropic_key")
            await sdk.shreckllm.set_anthropic_key(anthropic_key)

        # Validate provider credentials.
        openai = await sdk.shreckllm.validate_openai()
        anthropic = await sdk.shreckllm.validate_anthropic()
        print("openai_validation:", openai.model_dump())
        print("anthropic_validation:", anthropic.model_dump())

        # Ensure a known OpenAI model exists in provider model config.
        try:
            await sdk.shreckllm.add_provider_model("openai", "gpt-5-nano")
        except Exception as exc:
            print("add_provider_model(openai) skipped:", exc)

        # Print provider matrix and global readiness report.
        providers = await sdk.shreckllm.list_provider_statuses()
        print("providers_status:", [p.model_dump() for p in providers])

        preflight = await sdk.shreckllm.preflight_agents_llm_ready(strict=False)
        print("preflight_ready:", preflight.ready)
        print("preflight_reasons:", preflight.reasons)


if __name__ == "__main__":
    asyncio.run(main())
