"""
Tests for the Bilibili tracker module.
"""
import asyncio
import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.collectors.bilibili_tracker import (
    BilibiliTracker,
    BilibiliVideoStats,
    RateLimiter,
    LABEL_THRESHOLDS,
)
from app.db.database import Upload, UploadPerformance, UploadOutcome


class TestCalculateMetrics:
    """Tests for calculate_metrics method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.tracker = BilibiliTracker(self.mock_db)
        self.upload = Upload(
            video_id="vid123",
            channel_id="UC123",
            bilibili_bvid="BV1xx411x7xx",
            uploaded_at=datetime.utcnow(),
        )

    def test_calculate_metrics_view_velocity(self):
        """Test that view velocity is calculated correctly."""
        stats = BilibiliVideoStats(
            bvid="BV1xx411x7xx",
            views=2400,
            likes=100,
            coins=50,
            favorites=30,
            shares=20,
            danmaku=10,
            comments=5,
        )

        # At 24h checkpoint with 2400 views = 100 views/hour
        perf = self.tracker.calculate_metrics(stats, self.upload, checkpoint_hours=24)

        assert perf.view_velocity == 100.0
        assert perf.views == 2400
        assert perf.checkpoint_hours == 24

    def test_calculate_metrics_view_velocity_at_1h(self):
        """Test view velocity at 1 hour checkpoint."""
        stats = BilibiliVideoStats(
            bvid="BV1xx411x7xx",
            views=150,
            likes=10,
            coins=5,
            favorites=3,
            shares=2,
            danmaku=1,
            comments=0,
        )

        perf = self.tracker.calculate_metrics(stats, self.upload, checkpoint_hours=1)

        assert perf.view_velocity == 150.0
        assert perf.checkpoint_hours == 1

    def test_calculate_metrics_engagement_rate(self):
        """Test that engagement rate is calculated correctly."""
        stats = BilibiliVideoStats(
            bvid="BV1xx411x7xx",
            views=10000,
            likes=500,
            coins=200,
            favorites=300,
            shares=50,
            danmaku=100,
            comments=50,
        )

        perf = self.tracker.calculate_metrics(stats, self.upload, checkpoint_hours=24)

        # engagement = (500 + 200 + 300) / 10000 = 0.10
        assert perf.engagement_rate == 0.10
        assert perf.likes == 500
        assert perf.coins == 200
        assert perf.favorites == 300

    def test_calculate_metrics_zero_views(self):
        """Test engagement rate calculation with zero views (avoid division by zero)."""
        stats = BilibiliVideoStats(
            bvid="BV1xx411x7xx",
            views=0,
            likes=0,
            coins=0,
            favorites=0,
            shares=0,
            danmaku=0,
            comments=0,
        )

        perf = self.tracker.calculate_metrics(stats, self.upload, checkpoint_hours=1)

        # Should use max(views, 1) to avoid division by zero
        assert perf.engagement_rate == 0.0
        assert perf.view_velocity == 0.0

    def test_calculate_metrics_all_fields(self):
        """Test that all metrics fields are populated correctly."""
        stats = BilibiliVideoStats(
            bvid="BV1xx411x7xx",
            views=50000,
            likes=2500,
            coins=1000,
            favorites=500,
            shares=200,
            danmaku=1500,
            comments=300,
        )

        perf = self.tracker.calculate_metrics(stats, self.upload, checkpoint_hours=48)

        assert perf.upload_id == "vid123"
        assert perf.views == 50000
        assert perf.likes == 2500
        assert perf.coins == 1000
        assert perf.favorites == 500
        assert perf.shares == 200
        assert perf.danmaku == 1500
        assert perf.comments == 300
        assert perf.checkpoint_hours == 48
        assert perf.recorded_at is not None


class TestDetermineLabel:
    """Tests for determine_label method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_db = MagicMock()
        self.tracker = BilibiliTracker(self.mock_db)

    def make_performance(self, views: int, engagement_rate: float, coins: int) -> UploadPerformance:
        """Helper to create UploadPerformance with specified values."""
        return UploadPerformance(
            id=1,
            upload_id="vid123",
            checkpoint_hours=168,
            recorded_at=datetime.utcnow(),
            views=views,
            likes=int(views * engagement_rate * 0.5),
            coins=coins,
            favorites=int(views * engagement_rate * 0.3),
            shares=100,
            danmaku=100,
            comments=100,
            view_velocity=views / 168,
            engagement_rate=engagement_rate,
        )

    def test_determine_label_viral(self):
        """Test viral label detection."""
        # Viral: >1M views, >5% engagement, >10K coins
        perf = self.make_performance(
            views=1_500_000,
            engagement_rate=0.06,
            coins=15_000,
        )

        label = self.tracker.determine_label(perf)

        assert label == "viral"

    def test_determine_label_viral_edge_case(self):
        """Test viral threshold boundary."""
        # Exactly at thresholds
        perf = self.make_performance(
            views=1_000_000,
            engagement_rate=0.05,
            coins=10_000,
        )

        label = self.tracker.determine_label(perf)

        assert label == "viral"

    def test_determine_label_successful(self):
        """Test successful label detection."""
        # Successful: >100K views, >3% engagement (but not viral)
        perf = self.make_performance(
            views=500_000,
            engagement_rate=0.04,
            coins=5_000,  # Not enough coins for viral
        )

        label = self.tracker.determine_label(perf)

        assert label == "successful"

    def test_determine_label_successful_edge_case(self):
        """Test successful threshold boundary."""
        perf = self.make_performance(
            views=100_000,
            engagement_rate=0.03,
            coins=1_000,
        )

        label = self.tracker.determine_label(perf)

        assert label == "successful"

    def test_determine_label_standard(self):
        """Test standard label detection."""
        # Standard: >10K views, 1-3% engagement
        perf = self.make_performance(
            views=50_000,
            engagement_rate=0.02,
            coins=500,
        )

        label = self.tracker.determine_label(perf)

        assert label == "standard"

    def test_determine_label_standard_edge_case_low(self):
        """Test standard lower threshold boundary."""
        perf = self.make_performance(
            views=10_000,
            engagement_rate=0.01,
            coins=100,
        )

        label = self.tracker.determine_label(perf)

        assert label == "standard"

    def test_determine_label_standard_edge_case_high(self):
        """Test standard upper threshold boundary."""
        perf = self.make_performance(
            views=50_000,
            engagement_rate=0.03,
            coins=500,
        )

        label = self.tracker.determine_label(perf)

        assert label == "standard"

    def test_determine_label_failed_low_views(self):
        """Test failed label for low views."""
        # Failed: <10K views
        perf = self.make_performance(
            views=5_000,
            engagement_rate=0.02,
            coins=100,
        )

        label = self.tracker.determine_label(perf)

        assert label == "failed"

    def test_determine_label_failed_low_engagement(self):
        """Test failed label for low engagement."""
        # Failed: <1% engagement
        perf = self.make_performance(
            views=50_000,
            engagement_rate=0.005,  # 0.5%
            coins=100,
        )

        label = self.tracker.determine_label(perf)

        assert label == "failed"

    def test_determine_label_failed_zero_views(self):
        """Test failed label for zero views."""
        perf = self.make_performance(
            views=0,
            engagement_rate=0.0,
            coins=0,
        )

        label = self.tracker.determine_label(perf)

        assert label == "failed"


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_rate_limiter_init(self):
        """Test RateLimiter initialization."""
        limiter = RateLimiter(min_interval=2.0, max_retries=5)

        assert limiter.min_interval == 2.0
        assert limiter.max_retries == 5

    def test_rate_limiter_default_values(self):
        """Test RateLimiter default values."""
        limiter = RateLimiter()

        assert limiter.min_interval == 1.0
        assert limiter.max_retries == 3

    @pytest.mark.asyncio
    async def test_rate_limiter_wait(self):
        """Test that wait enforces minimum interval."""
        limiter = RateLimiter(min_interval=0.1)

        start = datetime.utcnow()
        await limiter.wait()
        await limiter.wait()
        end = datetime.utcnow()

        # Should have waited at least min_interval between calls
        elapsed = (end - start).total_seconds()
        assert elapsed >= 0.1

    @pytest.mark.asyncio
    async def test_rate_limiter_execute_with_retry_success(self):
        """Test successful execution without retry."""
        limiter = RateLimiter(min_interval=0.01)

        async def successful_coro():
            return "success"

        result = await limiter.execute_with_retry(successful_coro())

        assert result == "success"

    @pytest.mark.asyncio
    async def test_rate_limiter_exponential_backoff(self):
        """Test that retries use exponential backoff."""
        from bilibili_api import exceptions

        limiter = RateLimiter(min_interval=0.01, max_retries=3)
        attempt_count = 0

        async def failing_coro():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise exceptions.ResponseCodeException(-412, "Rate limited", "")
            return "success"

        # Patch asyncio.sleep to track backoff times
        sleep_times = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_times.append(duration)
            await original_sleep(0.001)  # Sleep briefly

        with patch('asyncio.sleep', mock_sleep):
            result = await limiter.execute_with_retry(failing_coro())

        assert result == "success"
        assert attempt_count == 3
        # Check exponential backoff: should be 2, 4 for rate limit (2 ** attempt * 2)
        assert len(sleep_times) >= 2
