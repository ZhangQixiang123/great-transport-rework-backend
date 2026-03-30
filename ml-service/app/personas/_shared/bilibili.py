"""Bilibili search — shared tool function."""
import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class BilibiliSearchResult:
    """A single Bilibili video from search results."""
    bvid: str
    title: str
    author: str
    views: int
    likes: int
    danmaku: int
    duration_seconds: int


def _parse_duration_text(text: str) -> int:
    """Parse Bilibili duration string like '12:34' or '1:02:34' to seconds."""
    parts = text.strip().split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(parts[0])
    except (ValueError, IndexError):
        return 0


async def search_bilibili(
    query: str,
    max_results: int = 10,
) -> List[BilibiliSearchResult]:
    """Search Bilibili for videos matching a query.

    Uses bilibili_api.search.search_by_type() for video search.
    """
    try:
        from bilibili_api import search

        resp = await search.search_by_type(
            keyword=query,
            search_type=search.SearchObjectType.VIDEO,
            page=1,
        )

        results = []
        for item in resp.get("result", [])[:max_results]:
            title = item.get("title", "")
            title = title.replace('<em class="keyword">', "").replace("</em>", "")

            duration_str = item.get("duration", "0:00")
            results.append(
                BilibiliSearchResult(
                    bvid=item.get("bvid", ""),
                    title=title,
                    author=item.get("author", ""),
                    views=int(item.get("play", 0)),
                    likes=int(item.get("like", 0)),
                    danmaku=int(item.get("danmaku", 0)),
                    duration_seconds=_parse_duration_text(duration_str),
                )
            )

        logger.info("Bilibili search '%s': %d results", query[:30], len(results))
        return results

    except Exception as e:
        logger.error("Bilibili search failed for '%s': %s", query[:30], e)
        return []
