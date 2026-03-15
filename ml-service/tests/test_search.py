"""
Tests for the search module (aggregator, re-exports, stubs).
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.search.aggregator import SearchAggregator, SearchCandidate


# ── SearchCandidate dataclass ─────────────────────────────────────────


class TestSearchCandidate:
    def test_create_with_defaults(self):
        """SearchCandidate has sensible defaults."""
        c = SearchCandidate(video_id="abc", title="Test", channel="Ch")
        assert c.video_id == "abc"
        assert c.views == 0
        assert c.likes == 0
        assert c.duration_seconds == 0
        assert c.category_id == 0
        assert c.opportunity_score == 0.0
        assert c.source_strategies == []
        assert c.source_queries == []

    def test_create_with_values(self):
        """SearchCandidate accepts all fields."""
        c = SearchCandidate(
            video_id="xyz",
            title="Full Video",
            channel="BigChannel",
            views=500_000,
            likes=20_000,
            duration_seconds=720,
            category_id=22,
            opportunity_score=0.85,
            source_strategies=["tech_review"],
            source_queries=["best laptop 2025"],
        )
        assert c.views == 500_000
        assert c.opportunity_score == 0.85
        assert c.source_strategies == ["tech_review"]


# ── SearchAggregator ──────────────────────────────────────────────────


class TestSearchAggregatorAdd:
    def test_add_single_candidate(self):
        """Adding a single candidate stores it."""
        agg = SearchAggregator()
        agg.add("v1", "Title 1", "Channel A", views=10000)
        assert agg.count() == 1

    def test_add_multiple_candidates(self):
        """Adding distinct video_ids creates separate entries."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "Ch1", views=10000)
        agg.add("v2", "T2", "Ch2", views=20000)
        agg.add("v3", "T3", "Ch3", views=30000)
        assert agg.count() == 3

    def test_add_with_strategy_and_query(self):
        """add() stores strategy and query in source lists."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "Ch1", strategy="music", query="piano covers")
        candidates = agg.get_candidates()
        assert candidates[0].source_strategies == ["music"]
        assert candidates[0].source_queries == ["piano covers"]


class TestSearchAggregatorDedup:
    def test_dedup_by_video_id(self):
        """Adding the same video_id twice does not create duplicates."""
        agg = SearchAggregator()
        agg.add("v1", "Title", "Channel", views=10000, opportunity_score=0.5)
        agg.add("v1", "Title", "Channel", views=10000, opportunity_score=0.3)
        assert agg.count() == 1

    def test_dedup_keeps_highest_opportunity_score(self):
        """When deduplicating, the highest opportunity_score is kept."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", opportunity_score=0.3, strategy="s1")
        agg.add("v1", "T", "C", opportunity_score=0.8, strategy="s2")

        candidates = agg.get_candidates()
        assert len(candidates) == 1
        assert candidates[0].opportunity_score == 0.8

    def test_dedup_lower_score_does_not_replace(self):
        """A lower opportunity_score does not replace the existing higher one."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", opportunity_score=0.9)
        agg.add("v1", "T", "C", opportunity_score=0.2)

        candidates = agg.get_candidates()
        assert candidates[0].opportunity_score == 0.9

    def test_dedup_merges_strategies(self):
        """When deduplicating, strategies from all sources are merged."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", strategy="music", query="q1")
        agg.add("v1", "T", "C", strategy="gaming", query="q2")

        candidates = agg.get_candidates()
        assert "music" in candidates[0].source_strategies
        assert "gaming" in candidates[0].source_strategies
        assert "q1" in candidates[0].source_queries
        assert "q2" in candidates[0].source_queries

    def test_dedup_no_duplicate_strategies(self):
        """Same strategy name is not added twice."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", strategy="music")
        agg.add("v1", "T", "C", strategy="music")

        candidates = agg.get_candidates()
        assert candidates[0].source_strategies.count("music") == 1

    def test_dedup_empty_strategy_not_added(self):
        """Empty strategy string is not added to source_strategies."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", strategy="")

        candidates = agg.get_candidates()
        assert candidates[0].source_strategies == []

    def test_dedup_empty_query_not_added(self):
        """Empty query string is not added to source_queries."""
        agg = SearchAggregator()
        agg.add("v1", "T", "C", query="")

        candidates = agg.get_candidates()
        assert candidates[0].source_queries == []


class TestSearchAggregatorGetCandidates:
    def test_sorted_by_opportunity_score_desc(self):
        """get_candidates returns candidates sorted by opportunity_score descending."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "C1", opportunity_score=0.3)
        agg.add("v2", "T2", "C2", opportunity_score=0.9)
        agg.add("v3", "T3", "C3", opportunity_score=0.6)

        candidates = agg.get_candidates()
        scores = [c.opportunity_score for c in candidates]
        assert scores == [0.9, 0.6, 0.3]

    def test_min_views_filter(self):
        """get_candidates filters out candidates below min_views."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "C1", views=5_000, opportunity_score=0.9)
        agg.add("v2", "T2", "C2", views=50_000, opportunity_score=0.5)
        agg.add("v3", "T3", "C3", views=100_000, opportunity_score=0.7)

        candidates = agg.get_candidates(min_views=10_000)
        assert len(candidates) == 2
        video_ids = [c.video_id for c in candidates]
        assert "v1" not in video_ids

    def test_min_views_zero_returns_all(self):
        """get_candidates with min_views=0 returns all candidates."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "C1", views=0)
        agg.add("v2", "T2", "C2", views=100)

        candidates = agg.get_candidates(min_views=0)
        assert len(candidates) == 2

    def test_empty_aggregator(self):
        """get_candidates on empty aggregator returns empty list."""
        agg = SearchAggregator()
        assert agg.get_candidates() == []


class TestSearchAggregatorCountAndClear:
    def test_count(self):
        """count() returns the number of unique candidates."""
        agg = SearchAggregator()
        assert agg.count() == 0
        agg.add("v1", "T1", "C1")
        assert agg.count() == 1
        agg.add("v2", "T2", "C2")
        assert agg.count() == 2
        # Duplicate should not increase count
        agg.add("v1", "T1", "C1")
        assert agg.count() == 2

    def test_clear(self):
        """clear() removes all candidates."""
        agg = SearchAggregator()
        agg.add("v1", "T1", "C1")
        agg.add("v2", "T2", "C2")
        assert agg.count() == 2

        agg.clear()
        assert agg.count() == 0
        assert agg.get_candidates() == []


# ── Re-exports from __init__.py ───────────────────────────────────────


class TestSearchReExports:
    def test_bilibili_search_result_importable(self):
        """BilibiliSearchResult is importable from app.search."""
        from app.search import BilibiliSearchResult
        assert BilibiliSearchResult is not None

    def test_youtube_similar_result_importable(self):
        """YouTubeSimilarResult is importable from app.search."""
        from app.search import YouTubeSimilarResult
        assert YouTubeSimilarResult is not None

    def test_search_aggregator_importable(self):
        """SearchAggregator is importable from app.search."""
        from app.search import SearchAggregator
        assert SearchAggregator is not None

    def test_web_rag_aggregator_importable(self):
        """WebRAGAggregator is importable from app.search."""
        from app.search import WebRAGAggregator
        assert WebRAGAggregator is not None

    def test_web_rag_context_importable(self):
        """WebRAGContext is importable from app.search."""
        from app.search import WebRAGContext
        assert WebRAGContext is not None


# ── Stub functions ────────────────────────────────────────────────────


class TestSearchStubs:
    @pytest.mark.asyncio
    async def test_search_youtube_keyword_returns_empty(self):
        """search_youtube_keyword stub returns empty list."""
        from app.search.youtube_keyword import search_youtube_keyword
        result = await search_youtube_keyword("test query")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_youtube_keyword_with_max_results(self):
        """search_youtube_keyword stub respects max_results param (still empty)."""
        from app.search.youtube_keyword import search_youtube_keyword
        result = await search_youtube_keyword("test query", max_results=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_check_followed_channels_returns_empty(self):
        """check_followed_channels stub returns empty list."""
        from app.search.channel_follow import check_followed_channels
        mock_db = MagicMock()
        result = await check_followed_channels(mock_db)
        assert result == []

    @pytest.mark.asyncio
    async def test_check_followed_channels_with_max(self):
        """check_followed_channels stub respects max_per_channel param."""
        from app.search.channel_follow import check_followed_channels
        mock_db = MagicMock()
        result = await check_followed_channels(mock_db, max_per_channel=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_find_cross_reference_gaps_returns_empty(self):
        """find_cross_reference_gaps stub returns empty list."""
        from app.search.cross_reference import find_cross_reference_gaps
        mock_db = MagicMock()
        result = await find_cross_reference_gaps(mock_db)
        assert result == []
