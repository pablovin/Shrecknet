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
