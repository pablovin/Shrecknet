import io
import json
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException
from app.api import deps


def test_browser_session_checks_subject_role_and_revocation(monkeypatch):
    profile = {"id": 1, "role": "player"}
    def response(request, timeout):
        assert request.get_header("Authorization") == "Bearer test-token"
        return io.BytesIO(json.dumps(profile).encode())
    monkeypatch.setattr(deps, "urlopen", response)
    deps._verify_browser_session("test-token", "1", "player")
    with pytest.raises(HTTPException) as mismatch:
        deps._verify_browser_session("test-token", "2", "player")
    assert mismatch.value.status_code == 401
    def revoked(request, timeout):
        raise HTTPError(request.full_url, 401, "revoked", {}, None)
    monkeypatch.setattr(deps, "urlopen", revoked)
    with pytest.raises(HTTPException) as denied:
        deps._verify_browser_session("test-token", "1", "player")
    assert denied.value.status_code == 401
