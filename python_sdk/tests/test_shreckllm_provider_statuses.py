import pytest

from shrecknet_client.resources import ShreckLLMAPI


class DummyClient:
    token = "abc"


@pytest.mark.asyncio
async def test_list_provider_statuses_includes_ollama_cloud() -> None:
    api = ShreckLLMAPI(DummyClient())

    async def fake_models():
        return {
            "providers": {
                "openai": {"models": ["gpt-5-nano"]},
                "ollama": {"models": ["gemma4:e4b"]},
                "ollama_cloud": {"models": ["gemma3:4b"]},
            }
        }

    async def fake_validate_provider(provider_id: str):
        payload = {
            "active": provider_id != "ollama_cloud" or True,
            "error": None,
        }
        from shrecknet_client.models import ProviderValidation

        return ProviderValidation.model_validate(payload)

    api.models = fake_models  # type: ignore[method-assign]
    api.validate_provider = fake_validate_provider  # type: ignore[method-assign]

    statuses = await api.list_provider_statuses()
    ids = [s.provider_id for s in statuses]

    assert ids == ["openai", "ollama", "ollama_cloud"]
    assert statuses[2].active is True
    assert statuses[2].models == ["gemma3:4b"]

    await api.aclose()
