"""Synthetic canvas-encoded2x2 fixtures, independently Chromium-decoded. No uploads/user media."""
import base64
import pytest

VALID_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAF0lEQVR4AWJSqVj7H4SZGIDgVMhtBgAAAAD//7zrmboAAAAGSURBVAMAQPQGjsPh4f4AAAAASUVORK5CYII=')
VALID_WEBP = base64.b64decode('UklGRjACAABXRUJQVlA4WAoAAAAgAAAAAQAAAQAASUNDUMgBAAAAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADZWUDggQgAAADACAJ0BKgIAAgAAwBIloAJ0ugH4AAMhBvuAAP71Y+9vMv7+1f3//ed7epUWh4Lbb//lnz+Gzqc/+sr+ej+ej/HCAA==')
VALID_JPEG = base64.b64decode('/9j/4AAQSkZJRgABAQAAAQABAAD/4gHYSUNDX1BST0ZJTEUAAQEAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADb/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAACAAIDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAdEAACAgIDAQAAAAAAAAAAAAABAwIEAAYFBxEh/8QAFQEBAQAAAAAAAAAAAAAAAAAABAb/xAAaEQABBQEAAAAAAAAAAAAAAAAAAQIDM3Gx/9oADAMBAAIRAxEAPwCjuv8Ar/V7uh63Ysa3xD3t42sxjW0VSlORVEmRJj6ST99xjGInufq9JuGtuIf/2Q==')


def test_avatar_upload_returns_static_url(client):
    response = client.post(
        '/uploads/avatars',
        files={'file': ('avatar.png', VALID_PNG, 'image/png')},
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
        files={'file': ('room.webp', VALID_WEBP, 'image/webp')},
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
        files={'file': ('world.jpg', VALID_JPEG, 'image/jpeg')},
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


@pytest.mark.parametrize("mime,payload", [("image/png", b"\x89PNG\r\n\x1a\nsmoke"),("image/webp", b"RIFFxxxxWEBPsmoke"),("image/jpeg", b"\xff\xd8\xff\xe0smoke")])
def test_truncated_signature_is_not_a_successful_image_upload(client,mime,payload):
    response=client.post("/uploads/avatars",files={"file":("invalid-image",payload,mime)})
    assert response.status_code == 422
