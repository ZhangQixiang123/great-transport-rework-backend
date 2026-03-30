"""HTTP client for submitting upload jobs to the Go yttransfer service."""
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Terminal statuses that indicate a job is done.
_TERMINAL_STATUSES = {"completed", "failed"}


class UploadClient:
    """Submits video upload requests to the Go upload service via HTTP."""

    def __init__(self, go_url: str = "http://localhost:8080"):
        self.go_url = go_url.rstrip("/")

    def submit_upload(
        self,
        video_id: str,
        title: str,
        description: str,
        tags: str = "",
    ) -> Dict:
        """POST an upload job to the Go service (returns immediately).

        The Go server returns 202 Accepted with {job_id, status: "pending"}.
        Use get_status() or poll_status() to track progress.

        Args:
            video_id: YouTube video ID.
            title: Video title for Bilibili.
            description: Full description text.
            tags: Comma-separated tags.

        Returns:
            Dict with keys: job_id, status, error (if any).
        """
        url = f"{self.go_url}/upload"
        payload = {
            "video_id": video_id,
            "title": title,
            "description": description,
            "tags": tags,
        }
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 409:
                logger.info("Duplicate upload for %s: %s", video_id, body)
            else:
                logger.error("Upload request failed (HTTP %d): %s", e.code, body)
            try:
                return json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return {"status": "duplicate" if e.code == 409 else "failed",
                        "error": f"HTTP {e.code}: {body}"}
        except urllib.error.URLError as e:
            logger.error("Cannot connect to Go service at %s: %s", self.go_url, e)
            return {"status": "failed", "error": f"Connection error: {e}"}
        except Exception as e:
            logger.error("Upload request error: %s", e)
            return {"status": "failed", "error": str(e)}

    def get_status(self, job_id: int) -> Dict:
        """GET the current status of an upload job.

        Args:
            job_id: The job ID returned by submit_upload().

        Returns:
            Dict with job details (job_id, video_id, status, title, etc.)
        """
        url = f"{self.go_url}/upload/status?id={job_id}"
        req = urllib.request.Request(url, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            return {"status": "failed", "error": f"HTTP {e.code}: {body}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    def poll_status(
        self,
        job_id: int,
        interval: float = 10,
        timeout: float = 1800,
    ) -> Dict:
        """Poll a job until it reaches a terminal status.

        Args:
            job_id: The job ID to poll.
            interval: Seconds between polls (default 10).
            timeout: Max seconds to wait (default 1800 = 30 min).

        Returns:
            Final job status dict.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.get_status(job_id)
            status = result.get("status", "")
            if status in _TERMINAL_STATUSES:
                return result
            time.sleep(interval)
        return {"status": "failed", "error": f"Polling timed out after {timeout}s"}

    def get_uploaded_ids(self) -> set[str]:
        """Fetch all already-uploaded video IDs from the Go service.

        Calls GET /upload/uploaded-ids. Returns empty set on error (non-blocking).
        """
        url = f"{self.go_url}/upload/uploaded-ids"
        req = urllib.request.Request(url, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                ids = json.loads(body)
                return set(ids) if isinstance(ids, list) else set()
        except Exception as e:
            logger.warning("Failed to fetch uploaded IDs from %s: %s", self.go_url, e)
            return set()

    def submit_batch(
        self,
        jobs: List[Dict],
        poll_interval: float = 10,
        poll_timeout: float = 1800,
    ) -> List[Dict]:
        """Submit multiple upload jobs, then poll all until complete.

        Args:
            jobs: List of dicts with keys: video_id, title, description, tags.
            poll_interval: Seconds between status polls (default 10).
            poll_timeout: Max seconds to wait for all jobs (default 1800).

        Returns:
            List of final status dicts (one per job, in order).
        """
        # Submit all jobs (non-blocking)
        submissions = []
        for j in jobs:
            result = self.submit_upload(
                video_id=j["video_id"],
                title=j["title"],
                description=j["description"],
                tags=j.get("tags", ""),
            )
            submissions.append(result)
            job_id = result.get("job_id")
            if job_id:
                logger.info("Submitted job %d for video %s", job_id, j["video_id"])
            else:
                logger.error("Failed to submit job for video %s: %s",
                             j["video_id"], result.get("error", "unknown"))

        # Poll all until terminal
        deadline = time.monotonic() + poll_timeout
        final_results: List[Optional[Dict]] = [None] * len(submissions)

        # Mark already-failed submissions
        for i, s in enumerate(submissions):
            if s.get("status") == "failed" or not s.get("job_id"):
                final_results[i] = s

        while time.monotonic() < deadline:
            all_done = True
            for i, s in enumerate(submissions):
                if final_results[i] is not None:
                    continue  # already terminal
                job_id = s.get("job_id")
                if not job_id:
                    continue
                result = self.get_status(job_id)
                if result.get("status") in _TERMINAL_STATUSES:
                    final_results[i] = result
                else:
                    all_done = False
            if all_done:
                break
            time.sleep(poll_interval)

        # Fill any still-pending with timeout error
        for i in range(len(final_results)):
            if final_results[i] is None:
                final_results[i] = {
                    "status": "failed",
                    "error": f"Polling timed out after {poll_timeout}s",
                    "job_id": submissions[i].get("job_id"),
                }

        return final_results
