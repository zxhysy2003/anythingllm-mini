from fastapi.testclient import TestClient

from app.main import app


def test_agent_ui_route_serves_static_page():
    client = TestClient(app)

    response = client.get("/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AnythingLLM Mini Agent" in response.text
    assert 'id="agent-app"' in response.text
    assert 'id="agent-form"' in response.text
    assert 'id="events"' in response.text
    assert "requestEventStream(" in response.text
    assert "parseSseBlock(block)" in response.text
    assert "appendAgentEvent(streamEvent)" in response.text
    assert "tool_confirmation_required" in response.text
    assert "policySummary(step)" in response.text
    assert "error_details" in response.text
    assert "artifactSummary(step)" in response.text
    assert "renderArtifacts(step)" in response.text
    assert "Artifact payload" in response.text
    assert "approved_tool_call_ids" in response.text
    assert "loadTrace(invocationId, article, sources)" in response.text
    assert "invocationToRun(invocation, sources)" in response.text
