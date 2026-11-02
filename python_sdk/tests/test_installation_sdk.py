import pytest
from shrecknet_client.installation import InstallationAPI, InstallationState, InstallationModels

@pytest.mark.asyncio
async def test_installation_validation_and_explicit_consent():
    class Client:
        def __init__(self): self.calls = []
        async def raw_request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))
            return InstallationState(world_id=7).model_dump()
    client = Client()
    api = InstallationAPI(client)
    with pytest.raises(ValueError):
        await api.verify(accept_provider_usage=False)
    assert client.calls == []
    assert (await api.verify(accept_provider_usage=True)).world_id == 7
    assert client.calls[-1] == ('POST', '/setup/installation/verify', {'json': {'accept_provider_usage': True}})
    with pytest.raises(ValueError):
        InstallationModels(provider='../config', model='m', jobs=['elder'])


@pytest.mark.asyncio
async def test_first_world_template_is_sent_with_example_disabled():
    class Client:
        async def raw_request(self, method, path, **kwargs):
            assert (method, path) == ('POST', '/setup/installation/world')
            assert kwargs == {'json': {'template': 'first_world', 'include_example': False}}
            return InstallationState(world_id=7, world_name='First World').model_dump()
    state = await InstallationAPI(Client()).create_world('first_world', include_example=False)
    assert state.world_name == 'First World'
