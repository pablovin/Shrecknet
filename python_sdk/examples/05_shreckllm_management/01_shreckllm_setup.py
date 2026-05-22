import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Verify shreckLLM connectivity and configure default provider/model.
# Expected result:
# - Prints status/health/ready, config default, applies patch, and reload confirmation.
async def main() -> None:
    base_url = os.getenv("SHRECKNET_BASE_URL", "http://localhost:8100")
    shreckllm_base_url = os.getenv("SHRECKLLM_BASE_URL", "http://localhost:8111")

    async with Shrecknet(base_url=base_url, shreckllm_base_url=shreckllm_base_url) as sdk:
        await ensure_user_and_login(sdk)

        # Check Shrecknet-facing llm status summary.
        status = await sdk.shreckllm.llm_status()
        print("llm_status:", status)

        # Check direct shreckLLM health and readiness.
        health = await sdk.shreckllm.health()
        ready = await sdk.shreckllm.ready()
        print("shreckllm_health:", health)
        print("shreckllm_ready:", ready)

        # Inspect runtime config and set a deterministic default provider/model.
        cfg = await sdk.shreckllm.get_config()
        print("config_default_provider:", cfg.get("default_provider_id"))

        providers = cfg.get("provider_defaults", {})
        if providers:
            first_provider = next(iter(providers.keys()))
            default_model = providers[first_provider].get("default_model")
            patched = await sdk.shreckllm.put_config(
                {"default_provider_id": first_provider, "provider_defaults": {first_provider: {"default_model": default_model}}}
            )
            print("patched_default_provider:", patched.get("default_provider_id"))

        # Reload to ensure config is applied from runtime store.
        reloaded = await sdk.shreckllm.reload_config()
        print("reloaded:", reloaded.get("reloaded"))


if __name__ == "__main__":
    asyncio.run(main())
