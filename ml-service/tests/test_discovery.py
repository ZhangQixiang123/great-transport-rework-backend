"""
Tests for the discovery pipeline modules.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.discovery.models import (
    Recommendation,
    RelevanceResult,
    TrendingKeyword,
    YouTubeCandidate,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with discovery tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    db.connect()
    db.ensure_discovery_tables()
    yield db
    db.close()
    os.unlink(db_path)


def _make_keyword(keyword="test", heat=100000, position=1):
    return TrendingKeyword(
        keyword=keyword,
        heat_score=heat,
        position=position,
        is_commercial=False,
    )


def _make_candidate(video_id="abc123", title="Test Video", views=50000):
    return YouTubeCandidate(
        video_id=video_id,
        title=title,
        channel_title="TestChannel",
        description="A test video description",
        views=views,
        likes=1000,
        comments=200,
        duration_seconds=600,
        category_id=22,
        tags=["test", "video"],
        published_at="2025-01-01T00:00:00Z",
        thumbnail_url="https://img.youtube.com/vi/abc123/hqdefault.jpg",
    )


def _make_recommendation(**kwargs):
    defaults = dict(
        keyword="test keyword",
        heat_score=500000,
        youtube_video_id="abc123",
        youtube_title="Test Video",
        youtube_channel="TestChannel",
        youtube_views=50000,
        youtube_likes=1000,
        youtube_duration_seconds=600,
        relevance_score=0.85,
        relevance_reasoning="Topic matches well",
        predicted_log_views=10.5,
        predicted_views=36000.0,
        predicted_label="successful",
        combined_score=0.75,
    )
    defaults.update(kwargs)
    return Recommendation(**defaults)


# ── Model Tests ───────────────────────────────────────────────────────


class TestTrendingKeyword:
    def test_create(self):
        kw = _make_keyword()
        assert kw.keyword == "test"
        assert kw.heat_score == 100000
        assert kw.is_commercial is False

    def test_commercial_flag(self):
        kw = TrendingKeyword("ad", 1000, 1, is_commercial=True)
        assert kw.is_commercial is True


class TestYouTubeCandidate:
    def test_create(self):
        c = _make_candidate()
        assert c.video_id == "abc123"
        assert c.views == 50000
        assert c.tags == ["test", "video"]


class TestRelevanceResult:
    def test_create_from_dict(self):
        r = RelevanceResult(
            relevance_score=0.8,
            reasoning="Good match",
            detected_topics=["gaming"],
            is_relevant=True,
        )
        assert r.relevance_score == 0.8
        assert r.is_relevant is True

    def test_json_roundtrip(self):
        r = RelevanceResult(
            relevance_score=0.6,
            reasoning="Partial match",
            detected_topics=["tech", "review"],
            is_relevant=True,
        )
        json_str = r.model_dump_json()
        r2 = RelevanceResult.model_validate_json(json_str)
        assert r2.relevance_score == 0.6
        assert r2.detected_topics == ["tech", "review"]


class TestRecommendation:
    def test_create(self):
        rec = _make_recommendation()
        assert rec.combined_score == 0.75
        assert rec.predicted_label == "successful"

    def test_nullable_predictions(self):
        rec = _make_recommendation(
            predicted_log_views=None,
            predicted_views=None,
            predicted_label=None,
        )
        assert rec.predicted_views is None


# ── Database Tests ────────────────────────────────────────────────────


class TestDiscoveryDB:
    def test_ensure_tables_idempotent(self, temp_db):
        # Calling twice should not raise
        temp_db.ensure_discovery_tables()
        temp_db.ensure_discovery_tables()

    def test_save_and_get_discovery_run(self, temp_db):
        run_id = temp_db.save_discovery_run(
            keywords_fetched=10,
            candidates_found=45,
            recommendations_count=12,
        )
        assert run_id is not None
        assert run_id > 0

        history = temp_db.get_discovery_history(limit=1)
        assert len(history) == 1
        assert history[0]["run_id"] == run_id
        assert history[0]["keywords_fetched"] == 10

    def test_save_recommendations(self, temp_db):
        run_id = temp_db.save_discovery_run(5, 20, 3)

        recs = [
            _make_recommendation(youtube_video_id="v1", combined_score=0.9),
            _make_recommendation(youtube_video_id="v2", combined_score=0.7),
            _make_recommendation(youtube_video_id="v3", combined_score=0.5),
        ]
        temp_db.save_recommendations(run_id, recs)

        history = temp_db.get_discovery_history(limit=1)
        top_recs = history[0]["top_recommendations"]
        assert len(top_recs) == 3
        # Should be ordered by combined_score DESC
        assert top_recs[0]["combined_score"] == 0.9
        assert top_recs[1]["combined_score"] == 0.7

    def test_empty_history(self, temp_db):
        history = temp_db.get_discovery_history()
        assert history == []


# ── Trending Tests ────────────────────────────────────────────────────


class TestFetchTrending:
    @pytest.mark.asyncio
    async def test_fetch_trending_filters_commercial(self):
        mock_response = {
            "list": [
                {
                    "keyword": "real_trend",
                    "heat_score": 500000,
                    "pos": 1,
                    "stat_datas": {"is_commercial": "0"},
                },
                {
                    "keyword": "ad_keyword",
                    "heat_score": 300000,
                    "pos": 2,
                    "stat_datas": {"is_commercial": "1"},
                },
                {
                    "keyword": "another_trend",
                    "heat_score": 200000,
                    "pos": 3,
                    "stat_datas": {"is_commercial": "0"},
                },
            ]
        }

        with patch(
            "app.discovery.trending.search.get_hot_search_keywords",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            from app.discovery.trending import fetch_trending_keywords

            keywords = await fetch_trending_keywords()

        assert len(keywords) == 2
        assert keywords[0].keyword == "real_trend"
        assert keywords[1].keyword == "another_trend"

    @pytest.mark.asyncio
    async def test_fetch_trending_empty(self):
        mock_response = {"list": []}

        with patch(
            "app.discovery.trending.search.get_hot_search_keywords",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            from app.discovery.trending import fetch_trending_keywords

            keywords = await fetch_trending_keywords()

        assert keywords == []


# ── YouTube Search Tests ──────────────────────────────────────────────


class TestYouTubeSearch:
    def test_parse_duration(self):
        from app.discovery.youtube_search import _parse_duration

        assert _parse_duration("PT1H2M3S") == 3723
        assert _parse_duration("PT5M30S") == 330
        assert _parse_duration("PT45S") == 45
        assert _parse_duration("PT1H") == 3600
        assert _parse_duration("") == 0
        assert _parse_duration(None) == 0

    @patch("app.discovery.youtube_search.httpx.Client")
    def test_search_youtube_videos(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        # Mock search response
        search_resp = MagicMock()
        search_resp.json.return_value = {
            "items": [{"id": {"videoId": "test_id_1"}}]
        }
        search_resp.raise_for_status = MagicMock()

        # Mock video details response
        details_resp = MagicMock()
        details_resp.json.return_value = {
            "items": [
                {
                    "id": "test_id_1",
                    "snippet": {
                        "title": "Test Video",
                        "channelTitle": "TestCh",
                        "description": "desc",
                        "categoryId": "22",
                        "publishedAt": "2025-01-01T00:00:00Z",
                        "tags": ["tag1"],
                        "thumbnails": {
                            "high": {"url": "https://example.com/thumb.jpg"}
                        },
                    },
                    "statistics": {
                        "viewCount": "1000",
                        "likeCount": "50",
                        "commentCount": "10",
                    },
                    "contentDetails": {"duration": "PT10M30S"},
                }
            ]
        }
        details_resp.raise_for_status = MagicMock()

        mock_client.get.side_effect = [search_resp, details_resp]

        from app.discovery.youtube_search import search_youtube_videos

        results = search_youtube_videos("test keyword", max_results=5)

        assert len(results) == 1
        assert results[0].video_id == "test_id_1"
        assert results[0].views == 1000
        assert results[0].duration_seconds == 630


# ── LLM Scorer Tests ─────────────────────────────────────────────────


class TestLLMScorer:
    @patch("app.discovery.llm_scorer.ollama")
    def test_score_relevance(self, mock_ollama):
        mock_ollama.list.return_value = MagicMock(
            models=[MagicMock(model="qwen2.5:7b")]
        )

        mock_response = MagicMock()
        mock_response.message.content = (
            '{"relevance_score": 0.85, "reasoning": "Good match", '
            '"detected_topics": ["gaming"], "is_relevant": true}'
        )
        mock_ollama.chat.return_value = mock_response

        from app.discovery.llm_scorer import LLMScorer

        scorer = LLMScorer(model="qwen2.5:7b")
        result = scorer.score_relevance("gaming", _make_candidate())

        assert result is not None
        assert result.relevance_score == 0.85
        assert result.is_relevant is True

    @patch("app.discovery.llm_scorer.ollama")
    def test_score_relevance_error_returns_none(self, mock_ollama):
        mock_ollama.list.return_value = MagicMock(
            models=[MagicMock(model="qwen2.5:7b")]
        )
        mock_ollama.chat.side_effect = Exception("Connection refused")

        from app.discovery.llm_scorer import LLMScorer

        scorer = LLMScorer(model="qwen2.5:7b")
        result = scorer.score_relevance("test", _make_candidate())

        assert result is None


# ── Pipeline Tests ────────────────────────────────────────────────────


class TestPipelineCombinedScore:
    def test_compute_combined_score_basic(self):
        from app.discovery.pipeline import DiscoveryPipeline

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        db = Database(db_path)
        db.connect()

        with patch("app.discovery.llm_scorer.ollama") as mock_ollama:
            mock_ollama.list.return_value = MagicMock(
                models=[MagicMock(model="qwen2.5:7b")]
            )
            pipeline = DiscoveryPipeline(db, model_dir="nonexistent")

        score = pipeline._compute_combined_score(
            heat_score=1_000_000,
            relevance=0.8,
            predicted_views=50000.0,
        )

        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Should be reasonably high with these inputs

        db.close()
        os.unlink(db_path)

    def test_compute_combined_score_no_prediction(self):
        from app.discovery.pipeline import DiscoveryPipeline

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        db = Database(db_path)
        db.connect()

        with patch("app.discovery.llm_scorer.ollama") as mock_ollama:
            mock_ollama.list.return_value = MagicMock(
                models=[MagicMock(model="qwen2.5:7b")]
            )
            pipeline = DiscoveryPipeline(db, model_dir="nonexistent")

        score = pipeline._compute_combined_score(
            heat_score=500000,
            relevance=0.7,
            predicted_views=None,
        )

        assert 0.0 <= score <= 1.0
        # With no prediction, views_weight uses neutral 0.5
        assert score > 0.3

        db.close()
        os.unlink(db_path)

    def test_compute_combined_score_zero_heat(self):
        from app.discovery.pipeline import DiscoveryPipeline

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        db = Database(db_path)
        db.connect()

        with patch("app.discovery.llm_scorer.ollama") as mock_ollama:
            mock_ollama.list.return_value = MagicMock(
                models=[MagicMock(model="qwen2.5:7b")]
            )
            pipeline = DiscoveryPipeline(db, model_dir="nonexistent")

        score = pipeline._compute_combined_score(
            heat_score=0,
            relevance=0.9,
            predicted_views=100000.0,
        )

        assert 0.0 <= score <= 1.0

        db.close()
        os.unlink(db_path)
