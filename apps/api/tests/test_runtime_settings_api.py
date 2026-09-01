def test_runtime_settings_default_and_conversation_override(client):
    default_response = client.get('/runtime-settings/default')
    assert default_response.status_code == 200
    default_data = default_response.json()
    assert default_data['model_key'] is None
    assert default_data['compression_model_key'] is None
    assert default_data['response_length_preset'] == 'medium'
    assert default_data['min_output_tokens'] == 768
    assert default_data['compression_interval_turns'] == 5
    assert [option['turns'] for option in default_data['compression_interval_options']] == [2, 3, 5, 8, 12]
    assert default_data['compression_interval_options'][0]['label'] == '2턴마다 · 강한 기억 유지'
    assert default_data['compression_interval_options'][0]['description'] == '새 방/관계 초반처럼 첫 상황과 말맛을 자주 고정해야 할 때'
    assert {preset['key'] for preset in default_data['response_length_presets']} == {'short', 'medium', 'long'}
    assert default_data['options'] == []
    assert default_data['compression_options'] == []

    patched_default = client.patch('/runtime-settings/default', json={'model_key': 'local-gemma', 'compression_model_key': 'gemini-3-flash', 'response_length_preset': 'short', 'compression_interval_turns': 8})
    assert patched_default.status_code == 400

    patched_default = client.patch('/runtime-settings/default', json={'response_length_preset': 'short', 'compression_interval_turns': 8})
    assert patched_default.status_code == 200
    assert patched_default.json()['model_key'] is None
    assert patched_default.json()['compression_model_key'] is None
    assert patched_default.json()['response_length_preset'] == 'short'
    assert patched_default.json()['min_output_tokens'] == 320
    assert patched_default.json()['compression_interval_turns'] == 8

    character = client.post('/characters', json={'name': '설정테스트', 'persona': '대화 설정 테스트용'}).json()
    conversation = client.post('/conversations', json={
        'mode': 'user_character',
        'participants': [
            {'type': 'user', 'id': 'user_001'},
            {'type': 'character', 'id': character['id']},
        ],
    }).json()

    inherited = client.get(f"/runtime-settings/conversations/{conversation['id']}")
    assert inherited.status_code == 200
    assert inherited.json()['model_key'] is None
    assert inherited.json()['compression_model_key'] is None
    assert inherited.json()['response_length_preset'] == 'short'
    assert inherited.json()['min_output_tokens'] == 320
    assert inherited.json()['compression_interval_turns'] == 8

    room_patch = client.patch(f"/runtime-settings/conversations/{conversation['id']}", json={'model_key': 'gemini-3-pro', 'compression_model_key': 'gemini-3-pro', 'response_length_preset': 'long', 'compression_interval_turns': 3})
    assert room_patch.status_code == 400


def test_flavor_and_world_rooms_inherit_global_compression_cadence(client):
    patched_default = client.patch('/runtime-settings/default', json={'compression_interval_turns': 8})
    assert patched_default.status_code == 200
    character = client.post('/characters', json={'name': '첫장면테스트', 'persona': '첫 장면 테스트용'}).json()
    conversation = client.post('/conversations', json={
        'mode': 'user_character',
        'title': '첫장면 자동 압축 방',
        'participants': [
            {'type': 'user', 'id': 'user_001'},
            {'type': 'character', 'id': character['id']},
        ],
        'scene': {
            'opening_scene': '서점 뒤편, 둘만 남은 밤',
            'opening_line': '늦었네. 그래도 기다렸어.',
            'tone_preset': 'slow_burn',
        },
    }).json()

    setting = client.get(f"/runtime-settings/conversations/{conversation['id']}")
    assert setting.status_code == 200
    assert setting.json()['compression_interval_turns'] == 8
    assert setting.json()['model_key'] is None
    assert setting.json()['response_length_preset'] == 'medium'

    world = client.post('/world-settings', json={
        'title': '압축 간격 상속 세계관',
        'world_seed': '세계관이 있어도 숨은 방별 압축 간격을 만들지 않는다.',
    }).json()
    world_room = client.post('/conversations', json={
        'mode': 'user_character',
        'world_setting_id': world['id'],
        'participants': [
            {'type': 'user', 'id': 'user_001'},
            {'type': 'character', 'id': character['id']},
        ],
    }).json()
    world_setting = client.get(f"/runtime-settings/conversations/{world_room['id']}")
    assert world_setting.status_code == 200
    assert world_setting.json()['compression_interval_turns'] == 8

    plain = client.post('/conversations', json={
        'mode': 'user_character',
        'title': '일반 방',
        'participants': [
            {'type': 'user', 'id': 'user_001'},
            {'type': 'character', 'id': character['id']},
        ],
    }).json()
    plain_setting = client.get(f"/runtime-settings/conversations/{plain['id']}").json()
    assert plain_setting['compression_interval_turns'] == 8
