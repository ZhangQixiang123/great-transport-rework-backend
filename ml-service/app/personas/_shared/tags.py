"""Tag generation from similar Bilibili videos — shared tool function."""
import logging
from collections import Counter
from dataclasses import dataclass
from typing import List

import httpx

logger = logging.getLogger(__name__)

_TAG_API = "https://api.bilibili.com/x/tag/archive/tags"
_TIMEOUT = 10
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.bilibili.com",
}


@dataclass
class BilibiliTag:
    tag_name: str
    tag_id: int = 0


async def fetch_video_tags(bvid: str) -> List[BilibiliTag]:
    """Fetch tags for a single Bilibili video by bvid."""
    try:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            resp = await client.get(_TAG_API, params={"bvid": bvid}, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0:
            logger.warning("Tag API error for %s: code=%s", bvid, data.get("code"))
            return []

        tags = []
        for item in data.get("data", []):
            tag_name = item.get("tag_name", "").strip()
            if tag_name:
                tags.append(BilibiliTag(tag_name=tag_name, tag_id=item.get("tag_id", 0)))
        return tags

    except Exception as e:
        logger.warning("Failed to fetch tags for %s: %s", bvid, e)
        return []


async def fetch_tags_for_videos(bvids: List[str]) -> dict[str, List[BilibiliTag]]:
    """Fetch tags for multiple videos concurrently."""
    import asyncio
    tasks = [fetch_video_tags(bvid) for bvid in bvids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    tags_by_video = {}
    for bvid, result in zip(bvids, results):
        if isinstance(result, Exception):
            tags_by_video[bvid] = []
        else:
            tags_by_video[bvid] = result
    return tags_by_video


def pick_best_tags(
    tags_by_video: dict[str, List[BilibiliTag]],
    max_tags: int = 10,
) -> List[str]:
    """Pick the best tags by frequency across similar videos."""
    counter: Counter[str] = Counter()
    for tags in tags_by_video.values():
        seen = set()
        for t in tags:
            if t.tag_name not in seen:
                counter[t.tag_name] += 1
                seen.add(t.tag_name)
    return [tag for tag, _count in counter.most_common(max_tags)]


async def generate_tags(
    query: str,
    max_similar: int = 10,
    max_tags: int = 10,
) -> List[str]:
    """End-to-end: search Bilibili for similar videos, fetch their tags,
    and return the best tags by frequency.
    """
    from .bilibili import search_bilibili

    try:
        similar = await search_bilibili(query, max_results=max_similar)
    except Exception as e:
        logger.warning("Bilibili search for tags failed: %s", e)
        return []

    if not similar:
        return []

    bvids = [r.bvid for r in similar if r.bvid]
    logger.info("Fetching tags from %d similar videos", len(bvids))

    tags_by_video = await fetch_tags_for_videos(bvids)
    best = pick_best_tags(tags_by_video, max_tags=max_tags)
    logger.info("Selected %d tags: %s", len(best), ", ".join(best))
    return best
