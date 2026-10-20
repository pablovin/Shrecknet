from pathlib import Path

import pytest
from pydantic import ValidationError
from shreckconfig import load_cors_policy


def test_policy_uses_only_file_without_environment_interpolation(tmp_path, monkeypatch):
    file = tmp_path / ".env"
    file.write_text('''CORS_ALLOW_ORIGINS=["https://table.example"]
CORS_ALLOW_CREDENTIALS=true
CORS_ALLOW_METHODS=["GET","POST"]
CORS_ALLOW_HEADERS=["Authorization","Content-Type"]
CORS_MAX_AGE=100
UNRELATED_SECRET=never-include
''')
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://untrusted.example"]')
    monkeypatch.setenv("SHRECKNET_CORS_ALLOW_ORIGINS", "invalid-json")
    monkeypatch.setenv("SHRECKWORLD_CORS_ALLOW_ORIGINS", "invalid-json")
    policy = load_cors_policy(file)
    assert policy.allow_origins == ["https://table.example"]
    assert policy.allow_credentials is True
    assert policy.max_age == 100
    assert "UNRELATED_SECRET" not in policy.model_dump()
    file.write_text('CORS_ALLOW_ORIGIN_REGEX=${RUNTIME_OVERRIDE}\n')
    monkeypatch.setenv("RUNTIME_OVERRIDE", ".*")
    assert load_cors_policy(file).allow_origin_regex == "${RUNTIME_OVERRIDE}"


def test_missing_file_denies_cross_origin_access(tmp_path):
    assert load_cors_policy(tmp_path / "missing").allow_origins == []


@pytest.mark.parametrize("value", ['not-json', '["*"]', '["https://table.example/path"]', '["https://user:secret@table.example"]', '["https://table.example/"]'])
def test_invalid_origins_fail_startup(tmp_path, value):
    file = tmp_path / ".env"
    file.write_text(f"CORS_ALLOW_ORIGINS={value}\n")
    with pytest.raises((ValueError, ValidationError)):
        load_cors_policy(file)


def test_changes_are_visible_on_reload(tmp_path):
    file = tmp_path / ".env"
    file.write_text('CORS_ALLOW_ORIGINS=["https://first.example"]\n')
    assert load_cors_policy(file).allow_origins == ["https://first.example"]
    file.write_text('CORS_ALLOW_ORIGINS=["https://second.example"]\n')
    assert load_cors_policy(file).allow_origins == ["https://second.example"]
