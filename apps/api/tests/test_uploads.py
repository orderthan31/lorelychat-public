def test_avatar_upload_returns_static_url(client):
    response = client.post(
        '/uploads/avatars',
        files={'file': ('avatar.png', b'\x89PNG\r\n\x1a\nsmoke', 'image/png')},
    )

    assert response.status_code == 200
    data = response.json()
    assert data['avatar_url'].endswith('.png')
    assert '/uploads/avatars/' in data['avatar_url']
    assert data['content_type'] == 'image/png'
    assert data['size'] > 0

    static_path = data['avatar_url'].split('testserver')[-1]
    static_response = client.get(static_path)
    assert static_response.status_code == 200
    assert static_response.content.startswith(b'\x89PNG')


def test_avatar_upload_rejects_non_image(client):
    response = client.post(
        '/uploads/avatars',
        files={'file': ('avatar.txt', b'not image', 'text/plain')},
    )

    assert response.status_code == 415


def test_conversation_thumbnail_upload_returns_static_url(client):
    response = client.post(
        '/uploads/conversation-thumbnails',
        files={'file': ('room.webp', b'RIFFxxxxWEBPsmoke', 'image/webp')},
    )

    assert response.status_code == 200
    data = response.json()
    assert data['thumbnail_url'].endswith('.webp')
    assert '/uploads/conversation-thumbnails/' in data['thumbnail_url']
    assert data['content_type'] == 'image/webp'
    assert data['size'] > 0

    static_path = data['thumbnail_url'].split('testserver')[-1]
    static_response = client.get(static_path)
    assert static_response.status_code == 200
    assert static_response.content.startswith(b'RIFF')


def test_world_thumbnail_upload_returns_static_url(client):
    response = client.post(
        '/uploads/world-thumbnails',
        files={'file': ('world.jpg', b'\xff\xd8\xff\xe0smoke', 'image/jpeg')},
    )

    assert response.status_code == 200
    data = response.json()
    assert data['thumbnail_url'].endswith('.jpg')
    assert '/uploads/world-thumbnails/' in data['thumbnail_url']
    assert data['content_type'] == 'image/jpeg'
    assert data['size'] > 0

    static_path = data['thumbnail_url'].split('testserver')[-1]
    static_response = client.get(static_path)
    assert static_response.status_code == 200
    assert static_response.content.startswith(b'\xff\xd8')
