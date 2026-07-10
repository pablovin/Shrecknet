import pytest

from shrecknet_client.errors import ConfigurationReadinessError
from shrecknet_client.models import ProviderStatus
from shrecknet_client.resources import ShreckLLMAPI


class DummyClient:
    token = "abc"


@pytest.mark.asyncio
async def test_strict_preflight_raises() -> None:
    api = ShreckLLMAPI(DummyClient())

    async def fake_reachable() -> bool:
        return False

    async def fake_list() -> list[ProviderStatus]:
        return []

    api.check_shreckllm_reachable = fake_reachable  # type: ignore[method-assign]
    api.list_provider_statuses = fake_list  # type: ignore[method-assign]

    with pytest.raises(ConfigurationReadinessError):
        await api.preflight_agents_llm_ready(strict=True)

    await api.aclose()


@pytest.mark.asyncio
async def test_preflight_uses_shreckllm_operational_flag() -> None:
    api = ShreckLLMAPI(DummyClient())
    list_calls = 0

    async def fake_reachable() -> bool:
        return True

    async def fake_operational() -> bool:
        return True

    async def fake_list() -> list[ProviderStatus]:
        nonlocal list_calls
        list_calls += 1
        return []

    api.check_shreckllm_reachable = fake_reachable  # type: ignore[method-assign]
    api.check_shreckllm_operational = fake_operational  # type: ignore[method-assign]
    api.list_provider_statuses = fake_list  # type: ignore[method-assign]

    report = await api.preflight_agents_llm_ready(strict=True)

    assert report.ready is True
    assert report.checks["shreckllm_operational"] is True
    assert list_calls == 1

    await api.aclose()
