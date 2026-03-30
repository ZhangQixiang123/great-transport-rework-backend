"""Fetch and aggregate tags from similar Bilibili videos.

Queries the public Bilibili API to get tags from existing videos,
then picks the most frequent ones as tags for a new upload.
"""
import logging
from collections import Counter
from dataclasses import dataclass
from typing import List

import httpx

logger = logging.getLogger(__name__)

_TAG_API = "https://api.bilibili.com/x/tag/archive/tags"
_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


@dataclass
class BilibiliTag:
    """A tag from a Bilibili video."""
    tag_name: str
    tag_id: int = 0


async def fetch_video_tags(bvid: str) -> List[BilibiliTag]:
    """Fetch tags for a single Bilibili video by bvid.

    Uses the public API (no auth required):
      GET https://api.bilibili.com/x/tag/archive/tags?bvid=...

    Returns:
        List of BilibiliTag, empty on failure.
    """
    try:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            resp = await client.get(
                _TAG_API,
                params={"bvid": bvid},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            logger.warning("Tag API error for %s: code=%s msg=%s",
                           bvid, data.get("code"), data.get("message"))
            return []

        tags = []
        for item in data.get("data", []):
            tag_name = item.get("tag_name", "").strip()
            if tag_name:
                tags.append(BilibiliTag(
                    tag_name=tag_name,
                    tag_id=item.get("tag_id", 0),
                ))
        return tags

    except Exception as e:
        logger.warning("Failed to fetch tags for %s: %s", bvid, e)
        return []


async def fetch_tags_for_videos(bvids: List[str]) -> dict[str, List[BilibiliTag]]:
    """Fetch tags for multiple videos concurrently.

    Returns:
        Dict mapping bvid -> list of tags.
    """
    import asyncio
    tasks = [fetch_video_tags(bvid) for bvid in bvids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    tags_by_video = {}
    for bvid, result in zip(bvids, results):
        if isinstance(result, Exception):
            logger.warning("Tag fetch exception for %s: %s", bvid, result)
            tags_by_video[bvid] = []
        else:
            tags_by_video[bvid] = result
    return tags_by_video


def pick_best_tags(
    tags_by_video: dict[str, List[BilibiliTag]],
    max_tags: int = 10,
) -> List[str]:
    """Pick the best tags by frequency across similar videos.

    Tags that appear on more similar videos are ranked higher.
    Bilibili allows up to 10 tags per video.

    Args:
        tags_by_video: Dict from bvid -> list of BilibiliTag.
        max_tags: Maximum number of tags to return.

    Returns:
        List of tag name strings, most frequent first.
    """
    counter: Counter[str] = Counter()
    for tags in tags_by_video.values():
        # Count each tag once per video (not duplicates within a video)
        seen = set()
        for t in tags:
            if t.tag_name not in seen:
                counter[t.tag_name] += 1
                seen.add(t.tag_name)

    # Return most common tags
    return [tag for tag, _count in counter.most_common(max_tags)]


async def generate_tags_from_similar(
    query: str,
    max_similar: int = 10,
    max_tags: int = 10,
) -> List[str]:
    """End-to-end: search Bilibili for similar videos, fetch their tags,
    and return the best tags by frequency.

    Args:
        query: Search query (typically the Chinese video title).
        max_similar: Max similar videos to search for.
        max_tags: Max tags to return.

    Returns:
        List of tag strings, most frequent first. Empty on failure.
    """
    from app.web_rag.bilibili_search import search_bilibili_similar

    # Step 1: Find similar Bilibili videos
    try:
        similar = await search_bilibili_similar(query, max_results=max_similar)
    except Exception as e:
        logger.warning("Bilibili search for tags failed: %s", e)
        return []

    if not similar:
        logger.info("No similar Bilibili videos found for tag generation")
        return []

    bvids = [r.bvid for r in similar if r.bvid]
    logger.info("Fetching tags from %d similar videos", len(bvids))

    # Step 2: Fetch tags from all similar videos
    tags_by_video = await fetch_tags_for_videos(bvids)

    # Step 3: Pick best by frequency
    best = pick_best_tags(tags_by_video, max_tags=max_tags)
    logger.info("Selected %d tags: %s", len(best), ", ".join(best))
    return best
