import asyncio
import os
from importlib import import_module

from shrecknet_client import Shrecknet

ensure_user_and_login = import_module("python_sdk.examples.01_login_and_user_creation.00_user_registration").ensure_user_and_login


# Purpose:
# - Verify shreckLLM connectivity and reload runtime configuration.
# Expected result:
# - Prints status/health/ready, provider config, and reload confirmation.
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

        # Inspect runtime provider config.
        cfg = await sdk.shreckllm.get_config()
        print("config_providers:", list((cfg.get("provider_defaults") or {}).keys()))

        # Reload to ensure config is applied from runtime store.
        reloaded = await sdk.shreckllm.reload_config()
        print("reloaded:", reloaded.get("reloaded"))


if __name__ == "__main__":
    asyncio.run(main())
