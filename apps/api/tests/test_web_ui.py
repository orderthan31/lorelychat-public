def test_embedded_static_frontend_was_removed_from_backend(client):
    response = client.get("/static/app.js")

    assert response.status_code == 404


def test_backend_root_points_to_separate_react_frontend(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["frontend"] == "Use the bundled web app or run apps/web locally"
