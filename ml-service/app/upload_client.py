"""HTTP client for submitting upload jobs to the Go yttransfer service."""
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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
        """POST an upload job to the Go service.

        Args:
            video_id: YouTube video ID.
            title: Video title for Bilibili.
            description: Full description text.
            tags: Comma-separated tags.

        Returns:
            Dict with keys: job_id, status, bilibili_bvid, error.
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
            with urllib.request.urlopen(req, timeout=1800) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error("Upload request failed (HTTP %d): %s", e.code, body)
            try:
                return json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return {"status": "failed", "error": f"HTTP {e.code}: {body}"}
        except urllib.error.URLError as e:
            logger.error("Cannot connect to Go service at %s: %s", self.go_url, e)
            return {"status": "failed", "error": f"Connection error: {e}"}
        except Exception as e:
            logger.error("Upload request error: %s", e)
            return {"status": "failed", "error": str(e)}
