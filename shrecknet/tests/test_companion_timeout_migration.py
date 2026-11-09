"""One-time saved Companion deadline upgrade, transaction safety and later edits."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import config_store as config
from test_character_agent_model_config import isolated_config, store, stored


OLD = dict(zip(config.COMPANION_TIMEOUT_FIELDS, (4, 12, 3, 8, 20, 180)))
NEW = dict(zip(config.COMPANION_TIMEOUT_FIELDS, (600, 600, 600, 600, 615, 10200)))


def rows():
    with config._connect() as conn:
        return conn.execute(f'SELECT key,value,updated_at FROM {config.CONFIG_TABLE} ORDER BY key').fetchall()


def test_saved_deadlines_upgrade_together_without_changing_other_controls(isolated_config):
    store({**OLD, 'companion_output_tokens': 3333, 'companion_recovery_max_calls': 4})
    settings = config.load_settings()
    assert {key: getattr(settings,key) for key in NEW} == NEW
    values = stored(isolated_config)
    assert all(values[key] == value for key,value in NEW.items())
    assert values[config.MIGRATION_COMPANION_TIMEOUTS_V1_KEY] is True
    assert settings.companion_output_tokens == 3333 and settings.companion_recovery_max_calls == 4
    assert config.MIGRATION_COMPANION_TIMEOUTS_V1_KEY not in settings.model_dump()
    assert next(row[2] for row in rows() if row[0]=='companion_output_tokens') == 'original'


def test_migration_preserves_longer_round_and_turn_limits_and_is_idempotent(isolated_config):
    store({**OLD, 'companion_retrieval_timeout_s': 650, 'companion_turn_timeout_s': 10800})
    settings = config.load_settings()
    assert settings.companion_retrieval_timeout_s == 650 and settings.companion_turn_timeout_s == 10800
    before = rows()
    config.load_settings()
    assert rows() == before
    config.update_settings(OLD)
    settings = config.reload_settings()
    assert {key: getattr(settings,key) for key in OLD} == OLD
    assert stored(isolated_config)[config.MIGRATION_COMPANION_TIMEOUTS_V1_KEY] is True


def test_fresh_database_migrates_short_seed_once(isolated_config):
    (isolated_config/'seed.json').write_text(json.dumps(OLD))
    settings = config.load_settings()
    assert {key: getattr(settings,key) for key in NEW} == NEW
    config.update_settings(OLD)
    assert config.reload_settings().companion_tool_timeout_s == 4


def test_failed_upgrade_rolls_back_values_and_marker(isolated_config):
    store(OLD)
    before = rows()
    with config._connect() as conn:
        conn.execute(f"""CREATE TRIGGER reject_timeout BEFORE UPDATE ON {config.CONFIG_TABLE}
            WHEN NEW.key='companion_web_timeout_s'
            BEGIN SELECT RAISE(ABORT,'injected migration failure'); END""")
    with pytest.raises(sqlite3.IntegrityError,match='injected migration failure'):
        config.load_settings()
    assert rows() == before
    assert config.MIGRATION_COMPANION_TIMEOUTS_V1_KEY not in stored(isolated_config)
    with config._connect() as conn:
        conn.execute('DROP TRIGGER reject_timeout')
    assert config.load_settings().companion_tool_timeout_s == 600


def test_concurrent_migration_checks_share_the_persisted_marker(isolated_config):
    store(OLD)
    defaults = config._default_settings_dict()
    def upgrade(_):
        with config._connect() as conn:
            config._migrate_companion_timeouts(conn,defaults)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(upgrade,range(2)))
    values = stored(isolated_config)
    assert all(values[key] == value for key,value in NEW.items())
    assert values[config.MIGRATION_COMPANION_TIMEOUTS_V1_KEY] is True
