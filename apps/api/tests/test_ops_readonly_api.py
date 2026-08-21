def test_relationship_map_endpoint_is_deprecated(client):
    response = client.get("/relationship-map?conversation_id=conv_1")

    assert response.status_code == 410
    assert "deprecated" in response.json()["detail"]
    assert "/conversations/{conversation_id}/context" in response.json()["detail"]


def test_data_cleanup_summary_endpoint_is_deprecated(client):
    response = client.get("/data-cleanup/summary")

    assert response.status_code == 410
    assert "deprecated" in response.json()["detail"]
