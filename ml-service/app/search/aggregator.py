"""Search aggregator — combines results from multiple search sources, deduplicates."""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchCandidate:
    """A deduplicated candidate from multiple search sources."""
    video_id: str
    title: str
    channel: str
    views: int = 0
    likes: int = 0
    duration_seconds: int = 0
    category_id: int = 0
    opportunity_score: float = 0.0
    source_strategies: List[str] = field(default_factory=list)
    source_queries: List[str] = field(default_factory=list)


class SearchAggregator:
    """Aggregates and deduplicates search results from multiple sources.

    Deduplication key is video_id. When the same video is found by
    multiple queries, keeps the highest opportunity_score and records
    all source strategies.
    """

    def __init__(self):
        self._candidates: Dict[str, SearchCandidate] = {}

    def add(
        self,
        video_id: str,
        title: str,
        channel: str,
        views: int = 0,
        likes: int = 0,
        duration_seconds: int = 0,
        category_id: int = 0,
        opportunity_score: float = 0.0,
        strategy: str = "",
        query: str = "",
    ) -> None:
        """Add a candidate, deduplicating by video_id."""
        if video_id in self._candidates:
            existing = self._candidates[video_id]
            if opportunity_score > existing.opportunity_score:
                existing.opportunity_score = opportunity_score
            if strategy and strategy not in existing.source_strategies:
                existing.source_strategies.append(strategy)
            if query and query not in existing.source_queries:
                existing.source_queries.append(query)
        else:
            self._candidates[video_id] = SearchCandidate(
                video_id=video_id,
                title=title,
                channel=channel,
                views=views,
                likes=likes,
                duration_seconds=duration_seconds,
                category_id=category_id,
                opportunity_score=opportunity_score,
                source_strategies=[strategy] if strategy else [],
                source_queries=[query] if query else [],
            )

    def get_candidates(self, min_views: int = 0) -> List[SearchCandidate]:
        """Get deduplicated candidates, sorted by opportunity score descending.

        Args:
            min_views: Minimum view count filter.

        Returns:
            Sorted list of SearchCandidate.
        """
        candidates = [
            c for c in self._candidates.values()
            if c.views >= min_views
        ]
        return sorted(candidates, key=lambda c: c.opportunity_score, reverse=True)

    def count(self) -> int:
        """Number of unique candidates."""
        return len(self._candidates)

    def clear(self) -> None:
        """Clear all candidates."""
        self._candidates.clear()
