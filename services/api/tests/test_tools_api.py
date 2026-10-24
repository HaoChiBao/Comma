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
        names = listed.json()["tools"]
        assert "list_tasks" in names
        assert "submit_message" in names
        assert "get_bom" in names
        assert "get_plan" in names
        assert "get_approval" in names
        assert "cancel_task" in names
        assert "get_task" in names

        missing_text = client.post(
            "/tools/submit_message",
            json={},
            headers={"Authorization": "Bearer test-tool-token"},
        )
        assert missing_text.status_code == 400

        missing_task = client.post(
            "/tools/get_bom",
            json={"task_id": "not-a-uuid"},
            headers={"Authorization": "Bearer test-tool-token"},
        )
        assert missing_task.status_code == 400

        response = client.post("/tools/list_tasks", json={}, headers={"Authorization": "Bearer test-tool-token"})
        assert response.status_code == 200, response.text
        assert response.json()["tasks"] == []

        unknown = client.post("/tools/approve", json={}, headers={"Authorization": "Bearer test-tool-token"})
        assert unknown.status_code == 404
    finally:
        settings.tool_api_token = ""
