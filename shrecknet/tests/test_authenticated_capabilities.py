from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.deps import get_current_user
from app.api.routers import configurations


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_player_capabilities_are_allowlisted_and_require_authentication(monkeypatch, enabled):
    app = FastAPI()
    app.include_router(configurations.router)
    monkeypatch.setattr(configurations, 'get_settings', lambda: SimpleNamespace(
        enable_ai_agents=enabled, smtp_password='private-setting',
    ))
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7, role='player')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/config/capabilities')
        assert response.status_code == 200
        assert response.json() == {'enable_ai_agents': enabled}

        def denied():
            raise HTTPException(401, 'Authentication required')

        app.dependency_overrides[get_current_user] = denied
        assert (await client.get('/config/capabilities')).status_code == 401
