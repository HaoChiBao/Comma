from app.settings import settings


def test_tool_route_lists_acme_tasks_and_rejects_a_bad_token(client):
    settings.tool_api_token = "test-tool-token"
    try:
        missing = client.post("/tools/list_tasks", json={})
        assert missing.status_code == 401

        denied = client.post("/tools/list_tasks", json={}, headers={"Authorization": "Bearer nope"})
        assert denied.status_code == 401

        listed = client.get("/tools", headers={"Authorization": "Bearer test-tool-token"})
        assert listed.status_code == 200
        assert "list_tasks" in listed.json()["tools"]
        assert "get_task" in listed.json()["tools"]

        response = client.post("/tools/list_tasks", json={}, headers={"Authorization": "Bearer test-tool-token"})
        assert response.status_code == 200, response.text
        assert response.json()["tasks"] == []

        unknown = client.post("/tools/approve", json={}, headers={"Authorization": "Bearer test-tool-token"})
        assert unknown.status_code == 404
    finally:
        settings.tool_api_token = ""
