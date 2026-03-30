"""Search candidate aggregation and deduplication."""
from dataclasses import dataclass, field
from typing import List


@dataclass
class SearchCandidate:
    """A deduplicated YouTube video candidate with scoring metadata."""
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
    """Deduplicates YouTube search results across multiple queries."""

    def __init__(self):
        self._candidates: dict[str, SearchCandidate] = {}

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
        if video_id in self._candidates:
            existing = self._candidates[video_id]
            if strategy and strategy not in existing.source_strategies:
                existing.source_strategies.append(strategy)
            if query and query not in existing.source_queries:
                existing.source_queries.append(query)
            existing.opportunity_score = max(
                existing.opportunity_score, opportunity_score
            )
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
        candidates = list(self._candidates.values())
        if min_views > 0:
            candidates = [c for c in candidates if c.views >= min_views]
        return sorted(candidates, key=lambda c: c.opportunity_score, reverse=True)

    def count(self) -> int:
        return len(self._candidates)

    def clear(self) -> None:
        self._candidates.clear()
