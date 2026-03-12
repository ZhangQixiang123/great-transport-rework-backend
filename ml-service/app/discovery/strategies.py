"""Transport strategy definitions and saturation checking.

Defines proven YouTube-to-Bilibili transport strategies and provides
functions to check how saturated each strategy is on Bilibili.
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, List, Optional

from ..web_rag.bilibili_search import BilibiliSearchResult

logger = logging.getLogger(__name__)

MIN_VIEWS_FOR_SATURATION = 10_000
SATURATION_THRESHOLD = 10  # >10 high-view videos in 30 days = saturated


@dataclass
class TransportStrategy:
    """A proven YouTube-to-Bilibili transport strategy."""
    name: str
    description: str
    example_queries: list[str]
    bilibili_check: str  # Chinese search term to check saturation

    # Filled in at runtime
    saturation_score: float = 0.0
    top_existing_videos: list[dict] = field(default_factory=list)


TRANSPORT_STRATEGIES: list[TransportStrategy] = [
    TransportStrategy(
        name="foreign_appreciation",
        description=(
            "Western creators showing genuine appreciation/analysis of Chinese culture, "
            "products, food, technology. Shows cultural difference with respect."
        ),
        example_queries=["foreigner tries Chinese street food", "western review Chinese EV"],
        bilibili_check="外国人 中国",
    ),
    TransportStrategy(
        name="educational_explainer",
        description=(
            "High-quality educational content on universal topics (science, space, "
            "history, engineering). Visual-heavy, language-independent."
        ),
        example_queries=["how black holes work explained", "engineering mega projects"],
        bilibili_check="科普 英文",
    ),
    TransportStrategy(
        name="cultural_comparison",
        description=(
            "Respectful comparison of how things differ between cultures. "
            "'How X works in America vs China', daily life differences."
        ),
        example_queries=["daily life difference America China", "school system comparison"],
        bilibili_check="中外对比",
    ),
    TransportStrategy(
        name="chinese_brand_foreign_review",
        description=(
            "Foreign creators reviewing Chinese brands/products (Huawei, BYD, "
            "Xiaomi, DJI). Chinese audiences love seeing international recognition."
        ),
        example_queries=["BYD review honest", "Huawei phone review 2026"],
        bilibili_check="外国人 评测 中国品牌",
    ),
    TransportStrategy(
        name="skill_talent_showcase",
        description=(
            "Pure skill/talent videos — music, art, cooking, engineering, sports. "
            "Visual content that transcends language barriers."
        ),
        example_queries=["incredible woodworking project", "street musician amazing performance"],
        bilibili_check="技术流 外国",
    ),
    TransportStrategy(
        name="behind_the_scenes",
        description=(
            "Factory tours, movie production, game development BTS. "
            "Satisfying, visual, universally appealing."
        ),
        example_queries=["how factory makes product", "game development behind scenes"],
        bilibili_check="幕后 制作过程",
    ),
    TransportStrategy(
        name="challenge_experiment",
        description=(
            "Scientific experiments, building challenges, survival projects. "
            "High entertainment value, visual, universal appeal."
        ),
        example_queries=["building challenge extreme", "science experiment unexpected result"],
        bilibili_check="挑战 实验",
    ),
    TransportStrategy(
        name="global_trending_chinese_angle",
        description=(
            "Global trending events/topics analyzed from a perspective that "
            "resonates with Chinese audiences. Tech drama, industry analysis."
        ),
        example_queries=["tech industry analysis 2026", "AI impact society"],
        bilibili_check="外网热议",
    ),
]


# Type alias for the bilibili search function
BilibiliSearchFn = Callable[[str, int], Awaitable[List[BilibiliSearchResult]]]


async def check_strategy_saturation(
    strategy: TransportStrategy,
    search_fn: BilibiliSearchFn,
    max_results: int = 20,
) -> float:
    """Check how saturated a strategy is on Bilibili.

    Searches Bilibili using the strategy's check term, counts videos
    with >MIN_VIEWS_FOR_SATURATION views, and returns a saturation score.

    Args:
        strategy: The transport strategy to check.
        search_fn: Async function to search Bilibili (query, max_results) -> results.
        max_results: Max search results to examine.

    Returns:
        Saturation score (0.0 = unsaturated, 1.0+ = saturated).
    """
    try:
        results = await search_fn(strategy.bilibili_check, max_results)
        high_view_count = sum(
            1 for r in results if r.views >= MIN_VIEWS_FOR_SATURATION
        )
        score = high_view_count / SATURATION_THRESHOLD
        strategy.saturation_score = score
        strategy.top_existing_videos = [
            {"title": r.title, "views": r.views, "bvid": r.bvid}
            for r in sorted(results, key=lambda x: x.views, reverse=True)[:5]
        ]
        logger.info(
            "Strategy '%s' saturation: %.2f (%d high-view videos)",
            strategy.name, score, high_view_count,
        )
        return score
    except Exception as e:
        logger.warning("Saturation check failed for '%s': %s", strategy.name, e)
        return 0.0


async def check_query_saturation(
    query: str,
    bilibili_check_query: str,
    search_fn: BilibiliSearchFn,
    max_results: int = 10,
    threshold: int = 5,
) -> bool:
    """Check if a specific query's content already exists abundantly on Bilibili.

    Args:
        query: The YouTube search query (for logging).
        bilibili_check_query: Chinese equivalent to search on Bilibili.
        search_fn: Async Bilibili search function.
        max_results: Max results to check.
        threshold: Number of high-view results that means saturated.

    Returns:
        True if the query topic is saturated on Bilibili.
    """
    try:
        results = await search_fn(bilibili_check_query, max_results)
        high_view_count = sum(
            1 for r in results if r.views >= MIN_VIEWS_FOR_SATURATION
        )
        saturated = high_view_count >= threshold
        if saturated:
            logger.info(
                "Query '%s' is saturated on Bilibili (%d high-view results)",
                query[:40], high_view_count,
            )
        return saturated
    except Exception as e:
        logger.warning("Query saturation check failed for '%s': %s", query[:40], e)
        return False


def get_unsaturated_strategies(
    strategies: list[TransportStrategy],
    max_saturation: float = 1.5,
) -> list[TransportStrategy]:
    """Filter and sort strategies by saturation (least saturated first).

    Args:
        strategies: List of strategies with saturation scores computed.
        max_saturation: Max saturation score to include (> this = skip).

    Returns:
        Sorted list of strategies below the saturation threshold.
    """
    filtered = [s for s in strategies if s.saturation_score <= max_saturation]
    return sorted(filtered, key=lambda s: s.saturation_score)
