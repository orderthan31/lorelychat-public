from sqlmodel import SQLModel

from app.services import system_prompt_service


def test_system_prompts_are_version_controlled_backend_source(client):
    settings = system_prompt_service.prompt_settings_map()

    assert 'generation_core_contract' in settings
    assert 'scene_relevance_gate' in settings
    assert 'appearance_reference_rules' in settings
    assert {
        'compression_fast_strategy',
        'compression_quality_extraction',
        'compression_quality_critic',
        'compression_quality_final',
        'compression_battle_guard',
    }.issubset(settings)
    assert 'lexicon_section_rules' not in settings
    assert 'Appearance/body/proportions/clothing are low-priority visual reference' in settings['scene_relevance_gate']
    assert 'actual room participants override character-card sample honorifics' in settings['dialogue_engine_base']
    assert 'direct address terms inside speech examples' in settings['single_character_identity_lock']
    assert 'not fixed counterpart roles' in settings['character_card_section_rules']
    assert 'JSON schema is only a transport envelope' in settings['xai_roleplay_rendering_contract']
    assert 'natural Korean roleplay prose first' in settings['xai_roleplay_rendering_contract']

    openapi_paths = client.get('/openapi.json').json()['paths']
    assert not any(path.startswith('/system-prompts') for path in openapi_paths)
    assert not any(path.startswith('/lexicon') for path in openapi_paths)


def test_prompt_registry_returns_fresh_runtime_map():
    first = system_prompt_service.prompt_settings_map()
    original = first['scene_relevance_gate']
    first['scene_relevance_gate'] = 'runtime mutation'

    second = system_prompt_service.prompt_settings_map()
    assert second['scene_relevance_gate'] == original
    assert 'runtime mutation' not in second.values()


def test_removed_feature_tables_are_not_registered():
    assert 'system_prompt_settings' not in SQLModel.metadata.tables
    assert 'lexicon_entries' not in SQLModel.metadata.tables
