"""Fresh model assignments work with one OpenRouter credential; saved choices win."""
import json
import sqlite3

import pytest

from app.core import config_store as config


@pytest.mark.parametrize('seed_kind', ['repository', 'installation', 'missing'])
def test_fresh_model_defaults_and_saved_assignment_precedence(tmp_path, monkeypatch, seed_kind):
    seed = config._repo_config_seed_file()
    if seed_kind != 'repository':
        seed = tmp_path / 'seed.json'
        if seed_kind == 'installation':
            seed.write_text('{"user_creation_mode":"stopped","enable_ai_agents":false}')
    monkeypatch.setattr(config, '_config_seed_file_candidates', lambda: [seed])
    database = tmp_path / 'config.db'
    monkeypatch.setattr(config, '_connect', lambda: sqlite3.connect(database))
    settings = config.load_settings()
    targets = {key: value for key, value in settings.model_dump().items()
               if key.startswith('model_') and isinstance(value, dict)}
    assert len(targets) == 23
    for target in targets.values():
        assert target['provider'] == 'openrouter'
        assert target['name'] == 'deepseek/deepseek-v4.1-flash'
    assert settings.enable_ai_agents is False
    assert settings.companion_enabled is False
    saved = {'provider': 'openai', 'name': 'gpt-4o-mini'}
    with sqlite3.connect(database) as connection:
        connection.execute(f'UPDATE {config.CONFIG_TABLE} SET value = ? WHERE key = ?',
                           (json.dumps(saved), 'model_elder_planner'))
    restored = config.load_settings()
    assert restored.model_elder_planner.provider == saved['provider']
    assert restored.model_elder_planner.name == saved['name']
