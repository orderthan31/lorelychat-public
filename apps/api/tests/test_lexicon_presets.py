def test_preset_crud_supports_room_seed_speech_style_and_compression_focus(client):
    room = client.post('/presets', json={
        'preset_type': 'room_seed',
        'title': '첫 만남 긴장감',
        'content': '두 캐릭터는 처음 만나 서로를 살짝 경계한다.',
        'description': '자동대화 시드용',
    })
    speech = client.post('/presets', json={
        'preset_type': 'speech_style',
        'title': '편한 반말 후배톤',
        'content': '사용자라고 부르고, 편한 반말을 쓴다.',
    })
    compression = client.post('/presets', json={
        'preset_type': 'compression_focus',
        'title': '관계/떡밥 압축',
        'content': '관계 변화, 약속, 경계선, 다음 장면 떡밥을 우선 보존한다.',
    })

    assert room.status_code == 200
    assert speech.status_code == 200
    assert compression.status_code == 200
    room_list = client.get('/presets?preset_type=room_seed').json()
    speech_list = client.get('/presets?preset_type=speech_style').json()
    compression_list = client.get('/presets?preset_type=compression_focus').json()
    assert room_list[0]['title'] == '첫 만남 긴장감'
    assert speech_list[0]['content'].startswith('사용자라고')
    assert compression_list[0]['title'] == '관계/떡밥 압축'

    patched = client.patch(f"/presets/{compression.json()['id']}", json={
        'preset_type': 'compression_focus',
        'title': '관계/떡밥 압축 수정',
        'content': '관계 변화와 미회수 떡밥을 최우선으로 보존한다.',
        'enabled': True,
    })
    assert patched.status_code == 200
    assert patched.json()['preset_type'] == 'compression_focus'
    assert patched.json()['title'] == '관계/떡밥 압축 수정'


def test_preset_detail_endpoint(client):
    preset = client.post('/presets', json={
        'preset_type': 'speech_style',
        'title': '편한 후배톤',
        'content': '사용자라고 부르고 편한 반말을 쓴다.',
    }).json()

    assert client.get(f"/presets/{preset['id']}").json()['title'] == '편한 후배톤'
    assert client.get('/presets/pre_missing').status_code == 404
