"""Tests for SSE endpoint (Stage 2)."""
import asyncio
import json

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
    sessions.clear()
    yield
    sessions.clear()


class TestSSE:

    def test_sse_sends_initial_state(self, client):
        s = PipelineSession()
        s.notify(PipelineStep.done, summary={"uploaded": 1})
        sessions[s.session_id] = s

        with client.stream("GET", f"/api/pipeline/{s.session_id}/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            # Read the first SSE event
            lines = []
            for line in resp.iter_lines():
                lines.append(line)
                if line == "":
                    break  # end of first event
                if len(lines) > 10:
                    break

            # Find data line
            data_lines = [l for l in lines if l.startswith("data: ")]
            assert len(data_lines) >= 1
            payload = json.loads(data_lines[0].removeprefix("data: "))
            assert payload["step"] == "done"
            assert payload["session_id"] == s.session_id

    def test_sse_404_for_missing_session(self, client):
        resp = client.get("/api/pipeline/missing/events")
        assert resp.status_code == 404
