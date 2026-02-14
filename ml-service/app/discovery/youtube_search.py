"""
Search YouTube for videos matching a keyword, with full stats enrichment.

Reuses the same API key and httpx patterns from enrich_youtube.py.
"""
import json
import logging
import re

import httpx

from .models import YouTubeCandidate

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = "AIzaSyAvCrdRnFYXwya6MIEdcN9jv4V-SxFYu1U"


def _parse_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def search_youtube_videos(
    keyword: str, max_results: int = 10
) -> list[YouTubeCandidate]:
    """Search YouTube for videos matching a keyword and fetch their stats.

    Uses YouTube Data API search.list (100 quota units per search),
    then videos.list to get full stats (1 unit per 50 videos).

    Args:
        keyword: Search query string.
        max_results: Max number of results to return (1-50).

    Returns:
        List of YouTubeCandidate with full stats populated.
    """
    client = httpx.Client()
    try:
        # Step 1: Search for video IDs
        resp = client.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": keyword,
                "type": "video",
                "maxResults": min(max_results, 50),
                "order": "relevance",
                "key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )
        resp.raise_for_status()
        search_data = resp.json()

        items = search_data.get("items", [])
        if not items:
            logger.info("No YouTube results for keyword: %s", keyword)
            return []

        video_ids = [item["id"]["videoId"] for item in items]

        # Step 2: Fetch full stats for all found videos
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
        video_data = resp.json()

        candidates = []
        for item in video_data.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            content = item.get("contentDetails", {})
            thumbnails = snippet.get("thumbnails", {})
            thumb = (
                thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url", "")
            )

            candidates.append(
                YouTubeCandidate(
                    video_id=item["id"],
                    title=snippet.get("title", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    description=snippet.get("description", ""),
                    views=int(stats.get("viewCount", 0)),
                    likes=int(stats.get("likeCount", 0)),
                    comments=int(stats.get("commentCount", 0)),
                    duration_seconds=_parse_duration(
                        content.get("duration", "")
                    ),
                    category_id=int(snippet.get("categoryId", 0)),
                    tags=snippet.get("tags", []),
                    published_at=snippet.get("publishedAt", ""),
                    thumbnail_url=thumb,
                )
            )

        logger.info(
            "Found %d YouTube videos for keyword: %s", len(candidates), keyword
        )
        return candidates

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.error("YouTube API quota exceeded")
        else:
            logger.error("YouTube API error: %s", e)
        return []
    except Exception as e:
        logger.error("YouTube search failed for '%s': %s", keyword, e)
        return []
    finally:
        client.close()
