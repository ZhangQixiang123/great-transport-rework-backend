"""Search YouTube for similar videos — same API pattern as youtube_search.py."""
import logging
import os
import re
from dataclasses import dataclass
from typing import List

import httpx

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.environ.get(
    "YOUTUBE_API_KEY", "AIzaSyAvCrdRnFYXwya6MIEdcN9jv4V-SxFYu1U"
)


@dataclass
class YouTubeSimilarResult:
    """A YouTube video from similarity search."""
    video_id: str
    title: str
    channel_title: str
    views: int
    likes: int
    comments: int
    duration_seconds: int


def _parse_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def search_youtube_similar(
    query: str,
    max_results: int = 10,
) -> List[YouTubeSimilarResult]:
    """Search YouTube for videos similar to a query.

    Args:
        query: Search query string.
        max_results: Maximum number of results.

    Returns:
        List of YouTubeSimilarResult with stats populated.
    """
    client = httpx.Client()
    try:
        # Step 1: Search
        resp = client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": min(max_results, 50),
                "order": "relevance",
                "key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return []

        video_ids = [item["id"]["videoId"] for item in items]

        # Step 2: Fetch full stats
        resp = client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )
        resp.raise_for_status()

        results = []
        for item in resp.json().get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})
            results.append(
                YouTubeSimilarResult(
                    video_id=item["id"],
                    title=snippet.get("title", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(stats.get("commentCount", 0)),
                    duration_seconds=_parse_duration(content.get("duration", "")),
                )
            )

        logger.info("YouTube similar search '%s': %d results", query[:30], len(results))
        return results

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.error("YouTube API quota exceeded")
        else:
            logger.error("YouTube API error: %s", e)
        return []
    except Exception as e:
        logger.error("YouTube similar search failed for '%s': %s", query[:30], e)
        return []
    finally:
        client.close()
