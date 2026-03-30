"""Upload client — shared tool function wrapping the Go service."""
import json
import logging
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"completed", "failed"}


def submit_upload(
    go_url: str,
    video_id: str,
    title: str,
    description: str,
    tags: str = "",
) -> Dict:
    """POST an upload job to the Go service (returns immediately).

    Returns dict with keys: job_id, status, error (if any).
    """
    url = f"{go_url.rstrip('/')}/upload"
    payload = {
        "video_id": video_id,
        "title": title,
        "description": description,
        "tags": tags,
    }
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url, data=data,
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
        logger.error("Cannot connect to Go service at %s: %s", go_url, e)
        return {"status": "failed", "error": f"Connection error: {e}"}
    except Exception as e:
        logger.error("Upload request error: %s", e)
        return {"status": "failed", "error": str(e)}


def get_upload_status(go_url: str, job_id: int) -> Dict:
    """GET the current status of an upload job."""
    url = f"{go_url.rstrip('/')}/upload/status?id={job_id}"
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


def poll_upload_status(
    go_url: str,
    job_id: int,
    interval: float = 10,
    timeout: float = 1800,
) -> Dict:
    """Poll a job until it reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get_upload_status(go_url, job_id)
        if result.get("status", "") in _TERMINAL_STATUSES:
            return result
        time.sleep(interval)
    return {"status": "failed", "error": f"Polling timed out after {timeout}s"}


def get_uploaded_ids(go_url: str) -> set[str]:
    """Fetch all already-uploaded video IDs from the Go service."""
    url = f"{go_url.rstrip('/')}/upload/uploaded-ids"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            ids = json.loads(body)
            return set(ids) if isinstance(ids, list) else set()
    except Exception as e:
        logger.warning("Failed to fetch uploaded IDs from %s: %s", go_url, e)
        return set()
