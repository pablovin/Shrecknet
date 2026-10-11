"""Four CharacterAgent model roles, config migration, and frontend contracts."""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.routers import configurations, llm_status
from app.core import config_store as config


def target(name, reasoning=False):
    return {'provider': 'openrouter', 'name': name, 'reasoning': reasoning}


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    seed = tmp_path / 'seed.json'
    seed.write_text('{}')
    monkeypatch.setenv('SHRECKNET_CONFIG_SEED_FILE', str(seed))
    monkeypatch.setattr(config, '_data_dir_cache', tmp_path)
    monkeypatch.setattr(config, '_settings_cache', None)
    monkeypatch.setattr(configurations, 'get_settings', config.get_settings)
    monkeypatch.setattr(configurations, 'update_settings', config.update_settings)
    monkeypatch.setattr(configurations, 'configure_celery_app', lambda: None)
    return tmp_path


def store(values):
    with config._connect() as conn:
        config._ensure_schema(conn)
        conn.executemany(f'INSERT INTO {config.CONFIG_TABLE} VALUES (?, ?, ?)',
                         [(key, json.dumps(value), 'original') for key, value in values.items()])


def stored(path):
    with sqlite3.connect(path / config.CONFIG_DB_FILENAME) as conn:
        return config._load_settings_from_db(conn)


@pytest.mark.parametrize('reasoning', [False, True])
def test_migration_preserves_roles_reasoning_and_is_idempotent(isolated_config, reasoning):
    old = {
        'model_character_agent_character_incorporation': target('incorporation', not reasoning),
        'model_character_agent_scene_interpretation': target('scene', reasoning),
        'model_character_agent_deliberation': target('decision', reasoning),
        'model_character_agent_framing': target('unused', True),
    }
    store(old)
    settings = config.load_settings()
    assert settings.model_character_agent_character_incorporation.model_dump() == old['model_character_agent_character_incorporation']
    assert settings.model_character_agent_scene_interpretation.model_dump() == target('scene', reasoning)
    assert settings.model_character_agent_psychological_interpretation.model_dump() == target('scene', reasoning)
    assert settings.model_character_agent_decision_making.model_dump() == target('decision', reasoning)
    first = stored(isolated_config)
    assert not set(config.RETIRED_CHARACTER_AGENT_MODELS) & set(first)
    config.load_settings()
    assert stored(isolated_config) == first


@pytest.mark.parametrize('canonical', [target('explicit', True), {'provider': '', 'name': '', 'reasoning': True}])
def test_explicit_new_targets_win_even_when_blank(isolated_config, canonical):
    store({'model_character_agent_deliberation': target('old-decision'),
           'model_character_agent_scene_interpretation': target('old-scene'),
           'model_character_agent_psychological_interpretation': canonical,
           'model_character_agent_decision_making': canonical})
    settings = config.load_settings()
    for key in config.CHARACTER_AGENT_MODEL_SOURCES:
        assert getattr(settings, key).model_dump() == canonical


def test_legacy_string_targets_and_unconfigured_install_migrate(isolated_config):
    store({'model_character_agent_deliberation': 'gpt-5',
           'model_character_agent_scene_interpretation': 'gpt-5-nano'})
    settings = config.load_settings()
    assert settings.model_character_agent_decision_making == config.LLMModelTarget(name='gpt-5')
    assert settings.model_character_agent_psychological_interpretation == config.LLMModelTarget(name='gpt-5-nano')


def test_invalid_migration_keeps_original_values_without_partial_writes(isolated_config):
    original = {'model_character_agent_deliberation': target('old-decision'),
                'model_character_agent_scene_interpretation': {'provider': [], 'name': 'invalid'},
                'model_character_agent_framing': target('unused')}
    store(original)
    with pytest.raises(ValueError):
        config.load_settings()
    assert stored(isolated_config) == original


def test_legacy_seed_is_accepted_and_new_keys_take_precedence(isolated_config, monkeypatch):
    seed = isolated_config / 'seed.json'
    seed.write_text(json.dumps({'model_character_agent_framing': target('unused'),
        'model_character_agent_deliberation': target('old', True),
        'model_character_agent_scene_interpretation': target('scene', True),
        'model_character_agent_decision_making': target('explicit')}))
    settings = config.load_settings()
    assert settings.model_character_agent_decision_making.name == 'explicit'
    assert settings.model_character_agent_psychological_interpretation == config.LLMModelTarget(**target('scene', True))
    assert not set(config.RETIRED_CHARACTER_AGENT_MODELS) & set(stored(isolated_config))


def test_four_targets_are_frontend_editable_with_individual_reasoning(isolated_config):
    settings = config.get_settings()
    schema = configurations.get_config_schema()
    group = next(group for group in schema['groups'] if group['id'] == 'character_agent')
    assert {key for key in group['fields'] if key.startswith('model_')} == set(config.CHARACTER_AGENT_MODEL_FIELDS)
    for key in config.CHARACTER_AGENT_MODEL_FIELDS:
        assert getattr(settings, key) == config.LLMModelTarget(provider='', name='')
        meta = schema['field_meta'][key]
        assert meta['frontend_editable'] is True and meta['change_impact'] == 'hot'
        assert meta['value_fields']['reasoning']['type'] == 'boolean'
        assert meta['value_fields']['reasoning']['default'] is False
    assert not set(config.RETIRED_CHARACTER_AGENT_MODELS) & set(schema['field_meta'])


@pytest.mark.asyncio
async def test_config_put_persists_independent_targets_and_reasoning(isolated_config):
    values = {key: target(f'model-{i}', bool(i % 2)) for i, key in enumerate(config.CHARACTER_AGENT_MODEL_FIELDS)}
    result = await configurations.put_config(values)
    reloaded = config.reload_settings()
    for key, value in values.items():
        assert result[key] == value
        assert getattr(reloaded, key).model_dump() == value
    key = config.CHARACTER_AGENT_MODEL_FIELDS[1]
    await configurations.put_config({key: {**values[key], 'reasoning': False}})
    assert getattr(config.reload_settings(), key).reasoning is False
    assert config.get_settings().model_character_agent_decision_making.reasoning is True


@pytest.mark.parametrize('key', config.RETIRED_CHARACTER_AGENT_MODELS)
def test_retired_inputs_are_rejected_not_silently_ignored(isolated_config, key):
    with pytest.raises(HTTPException) as exc:
        configurations._validate_updates({key: target('unused')})
    assert exc.value.status_code == 400
    with pytest.raises(ValueError, match='Retired'):
        config.update_settings({key: target('unused')})


@pytest.mark.parametrize('value', ['legacy-name', {'provider': 'openrouter', 'name': 'm', 'reasoning': 'invalid'}])
def test_bad_target_is_a_client_error_and_does_not_persist(isolated_config, value):
    before = config.get_settings().model_dump()
    with pytest.raises(HTTPException) as exc:
        configurations._validate_updates({'model_character_agent_psychological_interpretation': value})
    assert exc.value.status_code == 400
    assert config.reload_settings().model_dump() == before


def test_blank_target_preserves_explicit_reasoning(isolated_config):
    config.update_settings({'model_character_agent_decision_making': {'provider': '', 'name': '', 'reasoning': True}})
    assert config.reload_settings().model_character_agent_decision_making.reasoning is True


@pytest.mark.asyncio
async def test_status_exposes_only_active_roles_and_reasoning(isolated_config, monkeypatch):
    settings = config.get_settings()
    monkeypatch.setattr(llm_status, 'get_settings', lambda: settings)

    async def status(_):
        return {'reachable': False}

    async def providers(_):
        return {'providers': {}, 'operational_provider_ids': []}

    monkeypatch.setattr(llm_status, 'get_shreckllm_status', status)
    monkeypatch.setattr(llm_status, 'get_all_provider_validations', providers)
    result = await llm_status.get_service_status(None)
    actual = {key for key in result['model_targets'] if key.startswith('model_character_agent_')}
    assert actual == set(config.CHARACTER_AGENT_MODEL_FIELDS)
    assert all(result['model_targets'][key]['reasoning'] is False for key in actual)


@pytest.mark.asyncio
@pytest.mark.parametrize('reasoning', [False, True])
async def test_embodiment_routes_every_stage_to_its_distinct_target(reasoning):
    from app.jobs.character_agent.embody_agent import EmbodyAgent
    from app.schemas.character_agent import ProfileEventOutput
    from app.schemas.character_traits import TraitProfile
    from test_character_embodiment import BatchLLM, _canonical, _analysis, scenes

    class LLM(BatchLLM):
        async def chat(self, **kwargs):
            tag = kwargs['usage_tag']
            if tag.endswith('identity_description'):
                self.calls.append(kwargs)
                return json.dumps({'identity_summary': 'Identity', 'psychological_summary': 'Psychology', 'personality_traits': []})
            if tag.endswith('psychological_consolidation'):
                self.calls.append(kwargs)
                return json.dumps({'consolidation': {'aspect_operations': [], 'new_aspects': [],
                    'goal_operations': [], 'new_goals': [], 'focused_aspects': [], 'focused_goals': []}})
            return await super().chat(**kwargs)

    incorporation = config.LLMModelTarget(**target('identity-model', reasoning))
    scene = config.LLMModelTarget(**target('scene-model', not reasoning))
    psychology = config.LLMModelTarget(**target('psychology-model', reasoning))
    llm = LLM()
    job = EmbodyAgent(llm_client=llm, character_incorporation_model=incorporation,
                     scene_interpretation_model=scene, psychological_interpretation_model=psychology)
    profile = TraitProfile()
    identity = await job.generate_identity_description(canonical_identity=_canonical(),
        current_profile=profile, current_aspects=[], current_goals=[])
    canonical = _canonical({'identity_description': identity.model_dump(mode='json')})
    await job.analyze(source_entity_id='source', source_entity_alias='Source', canonical_identity=canonical,
                      current_trait_profile=profile, current_aspects=[], current_goals=[], scenes=scenes(1))
    await job._consolidate_profile(analysis=_analysis([ProfileEventOutput(kind='aspect', description='A change',
        scene_id='s0', evidence_ids=['scene:s0'])]), current_aspects=[], current_goals=[])
    expected = {'identity_description': incorporation, 'character_incorporation': scene,
                'scene_interpretation': psychology, 'trait_interpretation': psychology,
                'psychological_consolidation': scene}
    assert len(llm.calls) == 5
    for call in llm.calls:
        role = call['usage_tag'].split('.')[-1]
        assert call['model'] == expected[role]
        assert call['model'].reasoning == expected[role].reasoning
