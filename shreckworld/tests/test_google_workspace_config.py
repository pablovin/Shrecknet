from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException
from app.core.config import Settings
from app.services import google_workspace_config as config


def test_google_wizard_persists_without_replacing(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'world.db'}")
    uri = "https://example.test" + config.CALLBACK_PATH
    web = {"client_id": "test.apps.googleusercontent.com", "client_secret": "not-a-real-secret",
           "redirect_uris": [uri]}
    assert not config.configured(settings)
    saved = config.save(settings, web, uri)
    assert config.configured(saved)
    reread, source = config.resolve(settings)
    assert source == "wizard"
    assert reread.google_client_secret == web["client_secret"]
    assert reread.google_token_fernet_key == saved.google_token_fernet_key
    assert b"not-a-real-secret" not in (tmp_path / ".google-workspace/oauth.json").read_bytes()
    with pytest.raises(HTTPException) as exc:
        config.save(settings, web, uri)
    assert exc.value.status_code == 409


def test_google_wizard_rejects_mismatched_redirect(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'world.db'}")
    with pytest.raises(HTTPException) as exc:
        config.save(settings, {"client_id": "test.apps.googleusercontent.com",
                    "client_secret": "secret", "redirect_uris": []},
                    "https://example.test" + config.CALLBACK_PATH)
    assert exc.value.status_code == 422
    assert not (tmp_path / ".google-workspace").exists()
