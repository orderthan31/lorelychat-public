def test_backend_root_is_api_metadata_not_embedded_frontend(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "name": "Lorechat API",
        "frontend": "Use the bundled web app or run apps/web locally",
    }


def test_backend_allows_react_dev_origin(client):
    response = client.options(
        "/characters",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
