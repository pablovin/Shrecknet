# Installation API

Installation mode is opt-in. Use `sdk.installation.public_status()` to read only
whether it is enabled and still needs an owner. In a browser-session integration,
`BrowserAuthAPI(client, origin).claim_installation(claim_token=..., username=...,
full_name=..., email=..., password=...)` uses origin/CSRF protection and retains the
returned access token in the existing client. Retrieve the claim on the host,
never through an API or URL. After claiming, only administrators can run setup.

```python
from shrecknet_client import Shrecknet, InstallationModels

async with Shrecknet(base_url="http://localhost:8080/api/shrecknet") as sdk:
    await sdk.login("keeper", "your existing owner password")
    state = await sdk.installation.create_world("fantasy")
    # First configure/activate a real model through the ShreckLLM management API.
    state = await sdk.installation.configure_models(InstallationModels(
        provider="your-provider", model="your-configured-model", jobs=["elder"]
    ))
    # Explicit consent: these model checks can incur provider usage charges.
    state = await sdk.installation.verify(accept_provider_usage=True)
    state = await sdk.installation.status()  # poll until terminal; no auto-resubmit
    await sdk.installation.complete_introduction()
    # Completion requires a passed worker/model check if models were configured.
    state = await sdk.installation.complete()
```

`create_world` defaults to a labelled source/character/place/scene example, with
normal semantic indexing queued separately. Agent-specific input/preparation still
belongs to its domain API. A passed check verifies the queue/worker/model response
path, not every domain agent pipeline. Do not retry a timed-out paid request
without checking its saved status. Existing `/users/` bootstrap is protected by
the host claim when installation mode is enabled; it is unchanged outside that mode.

See [the backend contract](../../../Documentation/Deployment/FIRST_RUN.md).
