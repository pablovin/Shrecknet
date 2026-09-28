from __future__ import annotations

from app.api.routers import configurations


def test_config_schema_advertises_foundry_integrations_resource() -> None:
    schema = configurations.get_config_schema()

    assert schema["resources"] == [{
        "id": "foundry_integrations",
        "label": "Foundry integrations",
        "collection_url": "/config/integrations/foundry",
    }]
