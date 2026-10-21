import pytest

from shrecknet_client.models import CharacterAgentModelConfig, CharacterAgentModelConfigUpdate, LLMModelTarget
from shrecknet_client.resources import CharacterAgentsAPI


@pytest.mark.asyncio
async def test_model_configuration_read_and_partial_update_use_shrecknet_config():
    class Client:
        def __init__(self):
            self.calls = []
            self.values = {key: {'provider': 'openrouter', 'name': key, 'reasoning': False}
                           for key in CharacterAgentModelConfig.model_fields}

        async def raw_request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            if method == 'PUT':
                self.values.update(kwargs['json'])
            return {**self.values, 'app_name': 'unrelated-setting'}

    client = Client()
    api = CharacterAgentsAPI(client)
    initial = await api.get_model_config()
    assert client.calls[-1][:2] == ('GET', '/config/')
    assert initial.model_character_agent_decision_making.reasoning is False
    change = LLMModelTarget(provider='openrouter', name='selected-model', reasoning=True, reasoning_effort='low', web_search_enabled=True)
    result = await api.update_model_config(CharacterAgentModelConfigUpdate(
        model_character_agent_psychological_interpretation=change))
    assert client.calls[-1] == ('PUT', '/config/', {'json': {
        'model_character_agent_psychological_interpretation': change.model_dump()}})
    assert result.model_character_agent_psychological_interpretation == change
    assert result.model_character_agent_decision_making.reasoning is False
    assert 'app_name' not in result.model_dump()


def test_sdk_rejects_retired_models_and_bad_reasoning():
    with pytest.raises(ValueError):
        CharacterAgentModelConfigUpdate(model_character_agent_framing={'provider': '', 'name': ''})
    with pytest.raises(ValueError):
        LLMModelTarget(provider='openrouter', name='m', reasoning='invalid')


@pytest.mark.parametrize('effort', ['invalid', 'none', 'LOW', 1])
def test_sdk_rejects_invalid_reasoning_effort(effort):
    with pytest.raises(ValueError):
        LLMModelTarget(provider='openrouter', name='model', reasoning_effort=effort)
