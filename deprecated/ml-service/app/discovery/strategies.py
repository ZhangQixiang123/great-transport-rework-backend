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
        name="gaming_deep_dive",
        description=(
            "In-depth game reviews, industry controversies, broken launches, "
            "esports drama, competitive gaming analysis. Opinionated content with strong takes."
        ),
        example_queries=["Starfield was a disaster honest review", "worst game launches that killed studios", "esports match fixing scandal exposed"],
        bilibili_check="游戏评测 搬运",
    ),
    TransportStrategy(
        name="educational_explainer",
        description=(
            "High-quality educational content on universal topics (science, space, "
            "history, engineering). Visual-heavy, language-independent."
        ),
        example_queries=["why the ocean is still unexplored", "how bridges are built over deep water", "the engineering behind the tallest building"],
        bilibili_check="科普 英文",
    ),
    TransportStrategy(
        name="tech_teardown",
        description=(
            "In-depth electronics reviews, product teardowns, tech comparisons, "
            "planned obsolescence exposés, right-to-repair advocacy. Strong opinions, not unboxing fluff."
        ),
        example_queries=["iPhone teardown actual cost to make", "why your phone slows down on purpose", "right to repair Apple lost"],
        bilibili_check="数码评测 搬运",
    ),
    TransportStrategy(
        name="chinese_brand_foreign_review",
        description=(
            "Foreign creators reviewing Chinese brands/products (Huawei, BYD, "
            "Xiaomi, DJI). Chinese audiences love seeing international recognition."
        ),
        example_queries=["BYD seal test drive honest opinion", "foreigner tries Xiaomi for the first time", "DJI drone vs American competitor"],
        bilibili_check="外国人 评测 中国品牌",
    ),
    TransportStrategy(
        name="social_commentary",
        description=(
            "Societal debates, internet culture analysis, social experiments, "
            "workplace/generational issues. Content where creator takes a strong stance."
        ),
        example_queries=["giving strangers $1000 to see what happens", "why nobody talks to each other anymore", "the loneliness epidemic is getting worse"],
        bilibili_check="社会现象 外网",
    ),
    TransportStrategy(
        name="geopolitics_hot_take",
        description=(
            "International relations analysis, trade war breakdowns, sanctions impact, "
            "military/defense commentary. Political hot takes Chinese audiences care about."
        ),
        example_queries=["why US sanctions are backfiring explained", "the real reason China is winning in Africa", "NATO expansion consequences nobody talks about"],
        bilibili_check="国际政治 分析",
    ),
    TransportStrategy(
        name="challenge_experiment",
        description=(
            "Scientific experiments, building challenges, survival projects. "
            "High entertainment value, visual, universal appeal."
        ),
        example_queries=["I survived 24 hours in the wilderness with nothing", "building the strongest bridge out of popsicle sticks", "what happens if you microwave everything"],
        bilibili_check="挑战 实验",
    ),
    TransportStrategy(
        name="global_trending_chinese_angle",
        description=(
            "Global trending events/topics analyzed from a perspective that "
            "resonates with Chinese audiences. Tech drama, industry analysis."
        ),
        example_queries=["why everyone is mass quitting their jobs", "the AI tool that replaced 300 workers", "Boeing keeps getting worse and nobody cares"],
        bilibili_check="外网热议",
    ),
    TransportStrategy(
        name="surveillance_dashcam",
        description=(
            "Dashcam footage, surveillance clips, livestream fails/wins. "
            "The 神人TV genre — incredible or absurd real-life moments caught on camera."
        ),
        example_queries=["dashcam footage you won't believe is real", "security camera caught the most insane moment", "forklift operator destroys entire warehouse"],
        bilibili_check="监控 实况 神操作",
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
