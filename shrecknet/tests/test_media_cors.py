from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_media_library_path_includes_cors_headers_for_localhost_origin(client) -> None:
    origin = "http://localhost"
    response = await client.get(
        "/media/library/1/3/content.pdf",
        headers={"Origin": origin},
    )

    assert response.status_code in {200, 404}
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
