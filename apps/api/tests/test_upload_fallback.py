"""Run with --noconftest: the suite conftest imports the live app/lifespan."""

import hashlib

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app.core.config import Settings
from app.core.upload_files import UploadStaticFiles


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            path.stat().st_mtime_ns,
            path.stat().st_ino,
        )
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


@pytest.fixture
def uploads(tmp_path):
    primary = tmp_path / "primary"
    shared = tmp_path / "shared"
    primary.mkdir()
    shared.mkdir()
    (primary / "same.png").write_bytes(b"primary image")
    (shared / "same.png").write_bytes(b"shared image")
    (shared / "nested").mkdir()
    (shared / "nested" / "speech.mp3").write_bytes(b"shared speech")
    (tmp_path / "outside.txt").write_bytes(b"outside secret")
    for root in (primary, shared):
        (root / "outside-link.txt").symlink_to(tmp_path / "outside.txt")
        (root / "outside-dir").symlink_to(tmp_path, target_is_directory=True)
        (root / "index.html").write_text("<html>not served</html>")
        (root / "404.html").write_text("<html>not a fallback</html>")
        (root / "page.htm").write_text("<html>not served</html>")
        (root / "page.xhtml").write_text("<html>not served</html>")
    before = snapshot(tmp_path)
    app = Starlette(routes=[Mount("/uploads", app=UploadStaticFiles(primary, shared))])
    with TestClient(app) as client:
        yield client, primary, shared
    # Every read and rejected write must leave source contents and metadata alone.
    assert snapshot(tmp_path) == before


def test_primary_wins(uploads):
    client, _, _ = uploads
    response = client.get("/uploads/same.png")
    assert response.status_code == 200
    assert response.content == b"primary image"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_shared_get_and_head(uploads):
    client, primary, _ = uploads
    response = client.get("/uploads/nested/speech.mp3")
    head = client.head("/uploads/nested/speech.mp3")
    assert response.status_code == head.status_code == 200
    assert response.content == b"shared speech"
    assert head.content == b""
    for key in ("content-length", "content-type", "etag", "last-modified"):
        assert response.headers[key] == head.headers[key]
    assert not (primary / "nested").exists()


def test_missing_and_post(uploads):
    client, _, _ = uploads
    assert client.get("/uploads/missing.png").status_code == 404
    assert client.head("/uploads/missing.png").status_code == 404
    for path in ("same.png", "nested/speech.mp3", "new.png"):
        assert client.post(f"/uploads/{path}", content=b"overwrite").status_code == 405


@pytest.mark.parametrize("path", [
    "%2e%2e/outside.txt",
    "%2e%2e%2foutside.txt",
    "%2foutside.txt",
    "outside-link.txt",
    "outside-dir/outside.txt",
])
def test_escape_denied(uploads, path):
    client, primary, shared = uploads
    assert client.get(f"/uploads/{path}").status_code == 404
    # Exercise each root independently, including shared symlink containment.
    for root in (primary, shared):
        files = UploadStaticFiles(root)
        for target in ("../outside.txt", "/outside.txt", "outside-link.txt", "outside-dir/outside.txt"):
            assert files.lookup_path(target) == ("", None)


@pytest.mark.parametrize("path", ["", "index.html", "404.html", "page.htm", "page.xhtml"])
def test_no_html_serving(uploads, path):
    client, primary, shared = uploads
    for root in (primary, shared):
        app = Starlette(routes=[Mount("/uploads", app=UploadStaticFiles(root))])
        with TestClient(app) as single:
            assert single.get(f"/uploads/{path}").status_code == 404
    assert client.get(f"/uploads/{path}").status_code == 404


def test_optional_or_missing_shared_root(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "image.png").write_bytes(b"own")
    missing = tmp_path / "not-created"
    for shared in (None, missing):
        app = Starlette(routes=[Mount("/uploads", app=UploadStaticFiles(primary, shared))])
        with TestClient(app) as client:
            assert client.get("/uploads/image.png").content == b"own"
            assert client.get("/uploads/missing.png").status_code == 404
    assert not missing.exists()


def test_settings_default_and_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("SHARED_UPLOAD_ROOT", raising=False)
    assert Settings(_env_file=None).shared_upload_root is None
    monkeypatch.setenv("SHARED_UPLOAD_ROOT", str(tmp_path / "shared"))
    monkeypatch.setenv("UPLOAD_ROOT", str(tmp_path / "own"))
    settings = Settings(_env_file=None)
    assert settings.shared_upload_root == str(tmp_path / "shared")
    assert settings.upload_root == str(tmp_path / "own")
