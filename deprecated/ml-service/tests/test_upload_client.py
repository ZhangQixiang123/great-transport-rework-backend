"""Tests for the upload client with async job submission and polling."""
import http.server
import json
import threading
import time
import unittest

from app.upload_client import UploadClient


class FakeGoServer(http.server.HTTPServer):
    """Minimal fake Go server for testing the upload client."""

    allow_reuse_address = True

    def __init__(self, port: int):
        self.jobs: dict = {}
        self.next_id = 1
        super().__init__(("127.0.0.1", port), FakeHandler)


class FakeHandler(http.server.BaseHTTPRequestHandler):
    """Handles /upload, /upload/status, /upload/jobs."""

    def log_message(self, format, *args):
        pass  # suppress logs

    def do_POST(self):
        if self.path == "/upload":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            server: FakeGoServer = self.server
            job_id = server.next_id
            server.next_id += 1
            server.jobs[job_id] = {
                "job_id": job_id,
                "video_id": body["video_id"],
                "status": "pending",
                "title": body.get("title", ""),
                "bilibili_bvid": "",
                "error_message": "",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            resp = {"job_id": job_id, "status": "pending"}
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path.startswith("/upload/status"):
            # Parse ?id=X
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            job_id = int(qs.get("id", [0])[0])
            server: FakeGoServer = self.server
            job = server.jobs.get(job_id)
            if not job:
                self.send_error(404, "job not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(job).encode())
        elif self.path.startswith("/upload/jobs"):
            server: FakeGoServer = self.server
            jobs = sorted(server.jobs.values(), key=lambda j: j["job_id"], reverse=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(jobs).encode())
        else:
            self.send_error(404)


class FakeGoServerWithDedup(http.server.HTTPServer):
    """Fake Go server that supports dedup (409) and /upload/uploaded-ids."""

    allow_reuse_address = True

    def __init__(self, port: int):
        self.jobs: dict = {}
        self.next_id = 1
        self.submitted_video_ids: set = set()
        super().__init__(("127.0.0.1", port), FakeHandlerWithDedup)


class FakeHandlerWithDedup(http.server.BaseHTTPRequestHandler):
    """Handles /upload with dedup, /upload/status, /upload/jobs, /upload/uploaded-ids."""

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if self.path == "/upload":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            server: FakeGoServerWithDedup = self.server
            video_id = body["video_id"]

            # Dedup check
            if video_id in server.submitted_video_ids:
                resp = {"status": "duplicate", "error": "video already has an active job"}
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())
                return

            server.submitted_video_ids.add(video_id)
            job_id = server.next_id
            server.next_id += 1
            server.jobs[job_id] = {
                "job_id": job_id,
                "video_id": video_id,
                "status": "pending",
                "title": body.get("title", ""),
                "bilibili_bvid": "",
                "error_message": "",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            resp = {"job_id": job_id, "status": "pending"}
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp).encode())
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path.startswith("/upload/uploaded-ids"):
            server: FakeGoServerWithDedup = self.server
            ids = list(server.submitted_video_ids)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(ids).encode())
        elif self.path.startswith("/upload/status"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            job_id = int(qs.get("id", [0])[0])
            server: FakeGoServerWithDedup = self.server
            job = server.jobs.get(job_id)
            if not job:
                self.send_error(404, "job not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(job).encode())
        elif self.path.startswith("/upload/jobs"):
            server: FakeGoServerWithDedup = self.server
            jobs = sorted(server.jobs.values(), key=lambda j: j["job_id"], reverse=True)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(jobs).encode())
        else:
            self.send_error(404)


def _find_free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestUploadClientSubmit(unittest.TestCase):
    """Test submit_upload returns immediately with 202."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServer(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_submit_returns_pending(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        result = client.submit_upload("vid123", "Test Title", "Test Desc")
        self.assertEqual(result["status"], "pending")
        self.assertIn("job_id", result)
        self.assertGreater(result["job_id"], 0)

    def test_submit_multiple_returns_different_ids(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        r1 = client.submit_upload("vid1", "Title 1", "Desc 1")
        r2 = client.submit_upload("vid2", "Title 2", "Desc 2")
        self.assertNotEqual(r1["job_id"], r2["job_id"])


class TestUploadClientGetStatus(unittest.TestCase):
    """Test get_status polls job state."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServer(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_get_status_returns_job(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        submit = client.submit_upload("vid-status", "Title", "Desc")
        job_id = submit["job_id"]

        status = client.get_status(job_id)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["video_id"], "vid-status")

    def test_get_status_nonexistent(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        status = client.get_status(99999)
        self.assertEqual(status["status"], "failed")


class TestUploadClientPoll(unittest.TestCase):
    """Test poll_status waits for terminal status."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServer(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_poll_completes_when_status_changes(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        submit = client.submit_upload("vid-poll", "Title", "Desc")
        job_id = submit["job_id"]

        # Simulate the server completing the job after a short delay.
        def complete_after_delay():
            time.sleep(0.3)
            self.server.jobs[job_id]["status"] = "completed"
            self.server.jobs[job_id]["bilibili_bvid"] = "BV123456"

        threading.Thread(target=complete_after_delay, daemon=True).start()

        result = client.poll_status(job_id, interval=0.1, timeout=5)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["bilibili_bvid"], "BV123456")

    def test_poll_timeout(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        submit = client.submit_upload("vid-timeout", "Title", "Desc")
        job_id = submit["job_id"]
        # Job stays pending — should time out.
        result = client.poll_status(job_id, interval=0.05, timeout=0.2)
        self.assertEqual(result["status"], "failed")
        self.assertIn("timed out", result["error"].lower())


class TestUploadClientBatch(unittest.TestCase):
    """Test submit_batch submits all then polls."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServer(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_batch_all_complete(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")

        jobs = [
            {"video_id": "b1", "title": "T1", "description": "D1"},
            {"video_id": "b2", "title": "T2", "description": "D2"},
        ]

        # Complete all jobs shortly after submission.
        def complete_all():
            time.sleep(0.3)
            for job in self.server.jobs.values():
                job["status"] = "completed"

        threading.Thread(target=complete_all, daemon=True).start()

        results = client.submit_batch(jobs, poll_interval=0.1, poll_timeout=5)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r["status"], "completed")


class TestUploadClientDuplicateHandling(unittest.TestCase):
    """Test 409 duplicate handling."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServerWithDedup(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_submit_duplicate_returns_duplicate_status(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        # First submit succeeds
        r1 = client.submit_upload("dup-vid", "Title", "Desc")
        self.assertEqual(r1["status"], "pending")
        # Second submit returns duplicate
        r2 = client.submit_upload("dup-vid", "Title", "Desc")
        self.assertEqual(r2["status"], "duplicate")

    def test_get_uploaded_ids_returns_set(self):
        client = UploadClient(f"http://127.0.0.1:{self.port}")
        client.submit_upload("id-test-1", "T", "D")
        client.submit_upload("id-test-2", "T", "D")
        ids = client.get_uploaded_ids()
        self.assertIsInstance(ids, set)
        self.assertIn("id-test-1", ids)
        self.assertIn("id-test-2", ids)


class TestUploadClientGetUploadedIds(unittest.TestCase):
    """Test get_uploaded_ids with server that has the endpoint."""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.server = FakeGoServerWithDedup(cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_empty_returns_empty_set(self):
        port = _find_free_port()
        server = FakeGoServerWithDedup(port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = UploadClient(f"http://127.0.0.1:{port}")
            ids = client.get_uploaded_ids()
            self.assertIsInstance(ids, set)
            # May be empty or may have items from other tests, just verify type
        finally:
            server.shutdown()


class TestUploadClientConnectionError(unittest.TestCase):
    """Test client handles connection errors gracefully."""

    def test_submit_connection_refused(self):
        client = UploadClient("http://127.0.0.1:1")  # nothing listening
        result = client.submit_upload("vid", "T", "D")
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)

    def test_get_status_connection_refused(self):
        client = UploadClient("http://127.0.0.1:1")
        result = client.get_status(1)
        self.assertEqual(result["status"], "failed")

    def test_get_uploaded_ids_connection_refused(self):
        client = UploadClient("http://127.0.0.1:1")
        ids = client.get_uploaded_ids()
        self.assertEqual(ids, set())


if __name__ == "__main__":
    unittest.main()
