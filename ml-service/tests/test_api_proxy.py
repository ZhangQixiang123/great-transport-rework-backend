"""Tests for Go proxy endpoints (Stage 1)."""
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.server import app, GO_URL


@pytest.fixture
def client():
    # Initialize app.state that lifespan normally sets up
    app.state.http_client = httpx.AsyncClient(timeout=60.0)
    c = TestClient(app)
    yield c
    # Cleanup not strictly needed for sync tests, but good practice


class TestGoProxy:
    """Test Go proxy endpoints return 503 when Go is unreachable and forward correctly."""

    def test_uploads_returns_503_when_go_down(self, client):
        with respx.mock:
            respx.get(f"{GO_URL}/upload/jobs").mock(side_effect=httpx.ConnectError("refused"))
            resp = client.get("/api/uploads?limit=10")
            assert resp.status_code == 503
            assert "Go service unavailable" in resp.json()["error"]

    def test_uploads_forwards_response(self, client):
        go_data = [{"job_id": 1, "status": "completed"}]
        with respx.mock:
            respx.get(f"{GO_URL}/upload/jobs").mock(
                return_value=httpx.Response(200, json=go_data)
            )
            resp = client.get("/api/uploads?limit=50")
            assert resp.status_code == 200
            assert resp.json() == go_data

    def test_upload_status_proxy(self, client):
        go_data = {"job_id": 42, "status": "uploading"}
        with respx.mock:
            respx.get(f"{GO_URL}/upload/status").mock(
                return_value=httpx.Response(200, json=go_data)
            )
            resp = client.get("/api/uploads/42/status")
            assert resp.status_code == 200
            assert resp.json()["job_id"] == 42

    def test_uploaded_ids_proxy(self, client):
        go_data = ["abc123", "def456"]
        with respx.mock:
            respx.get(f"{GO_URL}/upload/uploaded-ids").mock(
                return_value=httpx.Response(200, json=go_data)
            )
            resp = client.get("/api/uploads/uploaded-ids")
            assert resp.status_code == 200
            assert resp.json() == go_data

    def test_create_upload_proxy(self, client):
        go_resp = {"job_id": 5, "status": "pending"}
        with respx.mock:
            respx.post(f"{GO_URL}/upload").mock(
                return_value=httpx.Response(202, json=go_resp)
            )
            resp = client.post(
                "/api/uploads",
                json={"video_id": "test123", "title": "Test"},
            )
            assert resp.status_code == 202

    def test_delete_upload_proxy(self, client):
        with respx.mock:
            respx.delete(f"{GO_URL}/upload/job").mock(
                return_value=httpx.Response(200, json={"deleted": 3})
            )
            resp = client.delete("/api/uploads/3")
            assert resp.status_code == 200

    def test_retry_subtitle_proxy(self, client):
        with respx.mock:
            respx.post(f"{GO_URL}/upload/retry-subtitle").mock(
                return_value=httpx.Response(202, json={"status": "generating"})
            )
            resp = client.post("/api/uploads/7/retry-subtitle")
            assert resp.status_code == 202

    def test_subtitle_preview_proxy(self, client):
        preview = {"english_srt": "...", "chinese_srt": "..."}
        with respx.mock:
            respx.get(f"{GO_URL}/upload/subtitle-preview").mock(
                return_value=httpx.Response(200, json=preview)
            )
            resp = client.get("/api/uploads/7/subtitle-preview")
            assert resp.status_code == 200
            assert resp.json() == preview

    def test_approve_subtitle_proxy(self, client):
        with respx.mock:
            respx.post(f"{GO_URL}/upload/subtitle-approve").mock(
                return_value=httpx.Response(202, json={"status": "approving"})
            )
            resp = client.post("/api/uploads/7/approve-subtitle")
            assert resp.status_code == 202

    def test_annotate_proxy(self, client):
        with respx.mock:
            respx.post(f"{GO_URL}/upload/annotate").mock(
                return_value=httpx.Response(202, json={"status": "annotating"})
            )
            resp = client.post("/api/uploads/7/annotate")
            assert resp.status_code == 202

    def test_go_error_forwarded(self, client):
        with respx.mock:
            respx.get(f"{GO_URL}/upload/status").mock(
                return_value=httpx.Response(404, text="job not found")
            )
            resp = client.get("/api/uploads/999/status")
            assert resp.status_code == 404
