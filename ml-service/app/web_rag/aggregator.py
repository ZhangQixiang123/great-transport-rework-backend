"""Aggregates Bilibili and YouTube search results into a unified context for LLM."""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

from .bilibili_search import BilibiliSearchResult, search_bilibili_similar
from .youtube_similar import YouTubeSimilarResult, search_youtube_similar

logger = logging.getLogger(__name__)


@dataclass
class SimilarVideo:
    """Unified similar video from either platform."""
    title: str
    views: int
    likes: int
    duration_seconds: int
    platform: str  # "bilibili" or "youtube"
    rank_position: int


@dataclass
class WebRAGContext:
    """Aggregated context from web search of both platforms."""
    similar_videos: List[SimilarVideo] = field(default_factory=list)
    query_used: str = ""

    @property
    def count(self) -> int:
        return len(self.similar_videos)

    def aggregate_stats(self) -> dict:
        """Compute aggregate statistics over similar videos."""
        if not self.similar_videos:
            return {
                "median_views": 0, "mean_views": 0, "std_views": 0,
                "max_views": 0, "min_views": 0,
                "median_log_views": 0.0, "mean_log_views": 0.0,
            }

        views = [v.views for v in self.similar_videos]
        log_views = [math.log1p(v) for v in views]

        sorted_views = sorted(views)
        n = len(sorted_views)
        median = sorted_views[n // 2] if n % 2 else (sorted_views[n // 2 - 1] + sorted_views[n // 2]) / 2

        mean_v = sum(views) / n
        std_v = (sum((v - mean_v) ** 2 for v in views) / n) ** 0.5

        sorted_log = sorted(log_views)
        median_log = sorted_log[n // 2] if n % 2 else (sorted_log[n // 2 - 1] + sorted_log[n // 2]) / 2

        return {
            "median_views": median,
            "mean_views": mean_v,
            "std_views": std_v,
            "max_views": max(views),
            "min_views": min(views),
            "median_log_views": median_log,
            "mean_log_views": sum(log_views) / n,
        }

    def format_for_llm(self) -> str:
        """Format context as text for LLM prompt injection."""
        if not self.similar_videos:
            return "No similar videos found."

        stats = self.aggregate_stats()
        lines = [
            f"Found {self.count} similar videos (query: {self.query_used}):",
            f"  Median views: {stats['median_views']:,.0f} | Mean: {stats['mean_views']:,.0f} | Max: {stats['max_views']:,.0f}",
            "",
            "Top similar videos:",
        ]
        for v in self.similar_videos[:10]:
            lines.append(
                f"  [{v.platform}] {v.title[:60]} — {v.views:,} views, "
                f"{v.likes:,} likes, {v.duration_seconds}s"
            )

        return "\n".join(lines)

    def to_reranker_features(self) -> List[dict]:
        """Convert similar videos to dicts for the neural reranker."""
        features = []
        for v in self.similar_videos:
            features.append({
                "log_views": math.log1p(v.views),
                "log_likes": math.log1p(v.likes),
                "duration": v.duration_seconds,
                "platform": 1.0 if v.platform == "bilibili" else 0.0,
                "rank_position": v.rank_position,
            })
        return features


class WebRAGAggregator:
    """Combines Bilibili and YouTube search results."""

    def __init__(
        self,
        bilibili_max: int = 10,
        youtube_max: int = 10,
    ):
        self.bilibili_max = bilibili_max
        self.youtube_max = youtube_max

    async def search(
        self,
        query: str,
        youtube_query: Optional[str] = None,
    ) -> WebRAGContext:
        """Search both platforms and combine results.

        Args:
            query: Search query (typically the video title or keyword in Chinese).
            youtube_query: Optional separate English query for YouTube.
                If None, uses `query` for both platforms.

        Returns:
            WebRAGContext with unified similar videos.
        """
        yt_query = youtube_query or query
        context = WebRAGContext(query_used=query)

        # Search Bilibili
        try:
            bili_results = await search_bilibili_similar(query, max_results=self.bilibili_max)
            for i, r in enumerate(bili_results):
                context.similar_videos.append(
                    SimilarVideo(
                        title=r.title,
                        views=r.views,
                        likes=r.likes,
                        duration_seconds=r.duration_seconds,
                        platform="bilibili",
                        rank_position=i + 1,
                    )
                )
        except Exception as e:
            logger.warning("Bilibili search failed: %s", e)

        # Search YouTube
        try:
            yt_results = search_youtube_similar(yt_query, max_results=self.youtube_max)
            for i, r in enumerate(yt_results):
                context.similar_videos.append(
                    SimilarVideo(
                        title=r.title,
                        views=r.views,
                        likes=r.likes,
                        duration_seconds=r.duration_seconds,
                        platform="youtube",
                        rank_position=i + 1,
                    )
                )
        except Exception as e:
            logger.warning("YouTube similar search failed: %s", e)

        logger.info(
            "WebRAG search '%s': %d total similar videos "
            "(%d bilibili, %d youtube)",
            query[:30],
            context.count,
            sum(1 for v in context.similar_videos if v.platform == "bilibili"),
            sum(1 for v in context.similar_videos if v.platform == "youtube"),
        )
        return context
