import json
import sqlite3

import pytest

from app.core import config_store as config
from app.api.routers import configurations
from app.jobs.character_agent.embody_agent import EmbodyAgent


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    monkeypatch.setenv("SHRECKNET_CONFIG_SEED_FILE", str(seed))
    monkeypatch.setattr(config, "_data_dir_cache", tmp_path)
    monkeypatch.setattr(config, "_settings_cache", None)
    return tmp_path


@pytest.mark.parametrize("value", [0, 1, 3])
def test_config_alias_and_canonical_precedence(value):
    assert config.Settings(**{config.LEGACY_VALIDATION_RETRIES: value}).character_agent_embodiment_validation_retries == value
    settings = config.Settings(**{config.LEGACY_VALIDATION_RETRIES: 3, config.VALIDATION_RETRIES: value})
    assert settings.character_agent_embodiment_validation_retries == value
    assert config.LEGACY_VALIDATION_RETRIES not in settings.model_dump()


@pytest.mark.parametrize("value", [-1, 4])
def test_retry_bounds(value):
    with pytest.raises(ValueError):
        config.Settings(**{config.VALIDATION_RETRIES: value})


@pytest.mark.parametrize("canonical", [None, 0])
def test_persisted_alias_migrates_before_default_seeding(isolated_config, canonical):
    conn = config._connect()
    config._ensure_schema(conn)
    values = {config.LEGACY_VALIDATION_RETRIES: 3}
    if canonical is not None:
        values[config.VALIDATION_RETRIES] = canonical
    conn.executemany(f"INSERT INTO {config.CONFIG_TABLE} VALUES (?, ?, ?)",
                     [(key, json.dumps(value), "now") for key, value in values.items()])
    conn.commit()
    conn.close()
    settings = config.load_settings()
    assert settings.character_agent_embodiment_validation_retries == (3 if canonical is None else canonical)
    with sqlite3.connect(isolated_config / config.CONFIG_DB_FILENAME) as conn:
        stored = config._load_settings_from_db(conn)
    assert config.LEGACY_VALIDATION_RETRIES not in stored
    assert stored[config.VALIDATION_RETRIES] == settings.character_agent_embodiment_validation_retries


def test_config_updates_normalize_alias_and_api_schema(isolated_config, monkeypatch):
    config.get_settings()
    monkeypatch.setattr(configurations, "get_settings", config.get_settings)
    payload = configurations._validate_updates({config.LEGACY_VALIDATION_RETRIES: 2})
    assert payload == {config.VALIDATION_RETRIES: 2}
    assert config.update_settings(payload).character_agent_embodiment_validation_retries == 2
    assert config.reload_settings().character_agent_embodiment_validation_retries == 2
    schema = configurations.get_config_schema()
    assert config.VALIDATION_RETRIES in str(schema)
    assert config.LEGACY_VALIDATION_RETRIES not in str(schema)
    both = configurations._validate_updates({config.LEGACY_VALIDATION_RETRIES: 3, config.VALIDATION_RETRIES: 0})
    assert config.update_settings(both).character_agent_embodiment_validation_retries == 0


def test_legacy_seed_preserves_custom_limit(isolated_config, monkeypatch):
    seed = isolated_config / "legacy-seed.json"
    seed.write_text(json.dumps({config.LEGACY_VALIDATION_RETRIES: 2}))
    monkeypatch.setenv("SHRECKNET_CONFIG_SEED_FILE", str(seed))
    assert config.load_settings().character_agent_embodiment_validation_retries == 2


def test_constructor_compatibility():
    args = dict(llm_client=None, character_incorporation_model="m", scene_interpretation_model="m")
    assert EmbodyAgent(**args, semantic_correction_attempts=2).validation_retries == 2
    assert EmbodyAgent(**args, validation_retries=0, semantic_correction_attempts=2).validation_retries == 0
    with pytest.raises(ValueError):
        EmbodyAgent(**args, validation_retries=4)


def test_deprecated_environment_alias(monkeypatch):
    monkeypatch.setenv("SHRECKNET_CHARACTER_AGENT_EMBODIMENT_SEMANTIC_CORRECTION_ATTEMPTS", "2")
    monkeypatch.delenv("SHRECKNET_CHARACTER_AGENT_EMBODIMENT_VALIDATION_RETRIES", raising=False)
    assert config.Settings().character_agent_embodiment_validation_retries == 2
    monkeypatch.setenv("SHRECKNET_CHARACTER_AGENT_EMBODIMENT_VALIDATION_RETRIES", "0")
    assert config.Settings().character_agent_embodiment_validation_retries == 0


def test_invalid_retry_limit_is_a_config_client_error(isolated_config, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(configurations, "get_settings", config.get_settings)
    with pytest.raises(HTTPException) as raised:
        configurations._validate_updates({config.VALIDATION_RETRIES: 4})
    assert raised.value.status_code == 400
