"""Tests for session-scoped pipeline API endpoints (Stage 2)."""
import pytest
from fastapi.testclient import TestClient

from app.server import app, sessions, PipelineSession, PipelineStep


@pytest.fixture
def client():
    import httpx
    app.state.http_client = httpx.AsyncClient(timeout=60.0)
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_sessions():
    """Clear sessions before each test."""
    sessions.clear()
    yield
    sessions.clear()


class TestPipelineAPI:

    def test_start_creates_session(self, client):
        resp = client.post(
            "/api/pipeline/start",
            json={"backend": "ollama", "dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"] in sessions

    def test_status_returns_session_state(self, client):
        s = PipelineSession()
        s.notify(PipelineStep.search)
        sessions[s.session_id] = s

        resp = client.get(f"/api/pipeline/{s.session_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["step"] == "search"
        assert data["session_id"] == s.session_id

    def test_status_404_for_missing_session(self, client):
        resp = client.get("/api/pipeline/missing-id/status")
        assert resp.status_code == 404

    def test_select_strategies_signals_event(self, client):
        s = PipelineSession()
        s.notify(PipelineStep.strategy_generation)
        s._strategy_event = __import__("asyncio").Event()
        sessions[s.session_id] = s

        resp = client.post(
            f"/api/pipeline/{s.session_id}/select-strategies",
            json={"names": ["gaming_deep_dive"]},
        )
        assert resp.status_code == 200
        assert s.selected_strategies == {"gaming_deep_dive"}

    def test_review_candidate(self, client):
        s = PipelineSession()
        s.candidates = [
            {"_id": "vid1", "video_id": "vid1", "title": "T", "description": "D",
             "strategy": "s", "tsundere_score": 5, "_approved": None, "_feedback": None,
             "candidate": None},
        ]
        s.notify(PipelineStep.review)
        sessions[s.session_id] = s

        resp = client.post(
            f"/api/pipeline/{s.session_id}/review/vid1",
            json={"approved": True},
        )
        assert resp.status_code == 200
        assert resp.json()["all_decided"] is True
        assert s.candidates[0]["_approved"] is True

    def test_review_wrong_phase(self, client):
        s = PipelineSession()
        s.notify(PipelineStep.search)
        sessions[s.session_id] = s

        resp = client.post(
            f"/api/pipeline/{s.session_id}/review/vid1",
            json={"approved": True},
        )
        assert resp.status_code == 400

    def test_list_sessions(self, client):
        s1 = PipelineSession()
        s2 = PipelineSession()
        sessions[s1.session_id] = s1
        sessions[s2.session_id] = s2

        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {s["session_id"] for s in data}
        assert s1.session_id in ids
        assert s2.session_id in ids

    def test_delete_session(self, client):
        s = PipelineSession()
        sessions[s.session_id] = s

        resp = client.delete(f"/api/pipeline/{s.session_id}")
        assert resp.status_code == 200
        assert s.session_id not in sessions

    def test_delete_nonexistent_session(self, client):
        resp = client.delete("/api/pipeline/nonexistent")
        assert resp.status_code == 404
