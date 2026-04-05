"""Tests for /api prefix routes (Stage 1)."""
import pytest
from fastapi.testclient import TestClient

from app.server import app


@pytest.fixture
def client():
    import httpx
    app.state.http_client = httpx.AsyncClient(timeout=60.0)
    return TestClient(app)


class TestApiRoutes:
    """Test that Python endpoints are accessible under /api prefix."""

    def test_skills_endpoint(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_review_stats_endpoint(self, client):
        resp = client.get("/api/review-stats")
        assert resp.status_code == 200

    def test_loop2_stats_endpoint(self, client):
        resp = client.get("/api/loop2/stats")
        assert resp.status_code == 200

    def test_sessions_endpoint(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_pipeline_start_returns_session_id(self, client):
        # Dry run with a non-existent persona should fail gracefully
        # but the endpoint itself should be reachable
        resp = client.post(
            "/api/pipeline/start",
            json={"backend": "ollama", "dry_run": True},
        )
        # It should return 200 with a session_id (pipeline starts in background)
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert len(data["session_id"]) > 0

    def test_pipeline_status_404_for_unknown_session(self, client):
        resp = client.get("/api/pipeline/nonexistent-session/status")
        assert resp.status_code == 404

    def test_pipeline_strategies_404_for_unknown_session(self, client):
        resp = client.get("/api/pipeline/nonexistent-session/strategies")
        assert resp.status_code == 404

    def test_pipeline_delete_404_for_unknown_session(self, client):
        resp = client.delete("/api/pipeline/nonexistent-session")
        assert resp.status_code == 404

    def test_old_routes_not_found(self, client):
        """Old routes without /api prefix should return 404."""
        resp = client.get("/status")
        assert resp.status_code in (404, 405)

        resp = client.post("/run", json={})
        assert resp.status_code in (404, 405)

        resp = client.get("/skills")
        assert resp.status_code in (404, 405)

    def test_go_start_stop_removed(self, client):
        """Go start/stop endpoints should no longer exist."""
        resp = client.post("/go/start")
        assert resp.status_code in (404, 405)

        resp = client.post("/go/stop")
        assert resp.status_code in (404, 405)
