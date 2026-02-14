"""
Fetch trending keywords from Bilibili hot search.
"""
import logging

from bilibili_api import search

from .models import TrendingKeyword

logger = logging.getLogger(__name__)


async def fetch_trending_keywords() -> list[TrendingKeyword]:
    """Fetch current hot search keywords from Bilibili.

    Returns:
        List of TrendingKeyword, filtered to exclude commercial/ad entries.
    """
    result = await search.get_hot_search_keywords()

    # The API returns "list" at the top level (not nested under data.trending)
    items = result.get("list", [])
    if not items:
        # Fall back to the nested path in case the API format changes back
        items = result.get("data", {}).get("trending", {}).get("list", [])

    keywords = []
    for item in items:
        keyword = item.get("keyword", "").strip()
        if not keyword:
            continue

        stat_datas = item.get("stat_datas", {})
        is_commercial = stat_datas.get("is_commercial", "0") == "1"
        tk = TrendingKeyword(
            keyword=keyword,
            heat_score=int(item.get("heat_score", 0)),
            position=int(item.get("pos", len(keywords))),
            is_commercial=is_commercial,
        )

        if not is_commercial:
            keywords.append(tk)
        else:
            logger.debug("Skipping commercial keyword: %s", keyword)

    logger.info("Fetched %d trending keywords (excluded commercial)", len(keywords))
    return keywords
