"""
Tests for Labeler module.
"""
import pytest
from datetime import datetime

from app.collectors.labeler import (
    calculate_engagement_rate,
    determine_label,
    Labeler,
    LABEL_THRESHOLDS,
)
from app.db.database import Database, CompetitorChannel, CompetitorVideo


def make_video(views: int, likes: int, coins: int, favorites: int) -> CompetitorVideo:
    """Helper to create a CompetitorVideo for testing."""
    return CompetitorVideo(
        bvid="BV123",
        bilibili_uid="12345",
        title="Test Video",
        description="",
        duration=300,
        views=views,
        likes=likes,
        coins=coins,
        favorites=favorites,
        shares=0,
        danmaku=0,
        comments=0,
        publish_time=None,
        collected_at=datetime.utcnow(),
        youtube_source_id=None,
        label=None
    )


class TestCalculateEngagementRate:
    """Tests for engagement rate calculation."""

    def test_normal_engagement(self):
        """Test normal engagement rate calculation."""
        video = make_video(views=100000, likes=3000, coins=1000, favorites=500)
        rate = calculate_engagement_rate(video)
        # (3000 + 1000 + 500) / 100000 = 0.045
        assert abs(rate - 0.045) < 0.001

    def test_zero_views_returns_zero(self):
        """Test that zero views returns zero engagement."""
        video = make_video(views=0, likes=100, coins=50, favorites=20)
        rate = calculate_engagement_rate(video)
        assert rate == 0.0

    def test_high_engagement(self):
        """Test high engagement rate."""
        video = make_video(views=10000, likes=1000, coins=500, favorites=500)
        rate = calculate_engagement_rate(video)
        # (1000 + 500 + 500) / 10000 = 0.20
        assert abs(rate - 0.20) < 0.001


class TestDetermineLabel:
    """Tests for label determination."""

    def test_viral_label(self):
        """Test viral label assignment."""
        # Viral: >1M views, >5% engagement, >10K coins
        video = make_video(views=2000000, likes=100000, coins=50000, favorites=30000)
        label = determine_label(video)
        assert label == "viral"

    def test_successful_label(self):
        """Test successful label assignment."""
        # Successful: >100K views, >3% engagement
        video = make_video(views=200000, likes=6000, coins=2000, favorites=1000)
        label = determine_label(video)
        assert label == "successful"

    def test_standard_label(self):
        """Test standard label assignment."""
        # Standard: >10K views, 1-3% engagement
        video = make_video(views=50000, likes=500, coins=200, favorites=100)
        label = determine_label(video)
        # (500 + 200 + 100) / 50000 = 0.016 = 1.6%
        assert label == "standard"

    def test_failed_label_low_views(self):
        """Test failed label for low views."""
        video = make_video(views=5000, likes=100, coins=50, favorites=20)
        label = determine_label(video)
        assert label == "failed"

    def test_failed_label_low_engagement(self):
        """Test failed label for low engagement."""
        video = make_video(views=50000, likes=100, coins=50, favorites=30)
        label = determine_label(video)
        # (100 + 50 + 30) / 50000 = 0.0036 = 0.36%
        assert label == "failed"

    def test_borderline_viral_not_enough_coins(self):
        """Test that high views without enough coins is not viral."""
        # High views and engagement but not enough coins
        video = make_video(views=1500000, likes=80000, coins=5000, favorites=30000)
        label = determine_label(video)
        # Not viral because coins < 10K, but successful because >100K views and >3% engagement
        assert label == "successful"


class TestLabelerIntegration:
    """Integration tests for Labeler with database."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a test database."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.connect()
        db.ensure_competitor_tables()
        yield db
        db.close()

    @pytest.fixture
    def labeler(self, db):
        """Create a Labeler instance."""
        return Labeler(db)

    def test_label_video(self, db, labeler):
        """Test labeling a single video."""
        # Add channel and video
        channel = CompetitorChannel(
            bilibili_uid="12345",
            name="Test",
            description="",
            follower_count=0,
            video_count=0,
            added_at=datetime.utcnow(),
            is_active=True
        )
        db.add_competitor_channel(channel)

        video = CompetitorVideo(
            bvid="BV123",
            bilibili_uid="12345",
            title="Test Video",
            description="",
            duration=300,
            views=150000,
            likes=5000,
            coins=2000,
            favorites=1000,
            shares=500,
            danmaku=1000,
            comments=300,
            publish_time=None,
            collected_at=datetime.utcnow(),
            youtube_source_id=None,
            label=None
        )
        db.save_competitor_video(video)

        # Label it
        label = labeler.label_video(video)
        assert label == "successful"

        # Verify in database
        videos = db.get_competitor_videos(label="successful")
        assert len(videos) == 1
        assert videos[0].bvid == "BV123"

    def test_label_all_unlabeled(self, db, labeler):
        """Test labeling all unlabeled videos."""
        # Add channel
        channel = CompetitorChannel(
            bilibili_uid="12345",
            name="Test",
            description="",
            follower_count=0,
            video_count=0,
            added_at=datetime.utcnow(),
            is_active=True
        )
        db.add_competitor_channel(channel)

        # Add multiple unlabeled videos
        videos_data = [
            ("BV001", 5000, 50, 20, 10),      # Failed
            ("BV002", 50000, 600, 200, 100),   # Standard
            ("BV003", 200000, 7000, 2500, 1500),  # Successful
        ]
        for bvid, views, likes, coins, favorites in videos_data:
            video = CompetitorVideo(
                bvid=bvid,
                bilibili_uid="12345",
                title=f"Video {bvid}",
                description="",
                duration=300,
                views=views,
                likes=likes,
                coins=coins,
                favorites=favorites,
                shares=0,
                danmaku=0,
                comments=0,
                publish_time=None,
                collected_at=datetime.utcnow(),
                youtube_source_id=None,
                label=None
            )
            db.save_competitor_video(video)

        # Label all
        results = labeler.label_all_unlabeled()
        assert results["total"] == 3
        assert results["failed"] == 1
        assert results["standard"] == 1
        assert results["successful"] == 1

    def test_get_label_distribution(self, db, labeler):
        """Test getting label distribution."""
        distribution = labeler.get_label_distribution()
        assert "total" in distribution
        assert "viral" in distribution
        assert "successful" in distribution
        assert "standard" in distribution
        assert "failed" in distribution
        assert "unlabeled" in distribution


class TestLabelThresholds:
    """Tests for label threshold definitions."""

    def test_viral_thresholds_exist(self):
        """Test viral thresholds are defined."""
        assert "viral" in LABEL_THRESHOLDS
        assert LABEL_THRESHOLDS["viral"]["min_views"] == 1_000_000
        assert LABEL_THRESHOLDS["viral"]["min_engagement_rate"] == 0.05
        assert LABEL_THRESHOLDS["viral"]["min_coins"] == 10_000

    def test_successful_thresholds_exist(self):
        """Test successful thresholds are defined."""
        assert "successful" in LABEL_THRESHOLDS
        assert LABEL_THRESHOLDS["successful"]["min_views"] == 100_000
        assert LABEL_THRESHOLDS["successful"]["min_engagement_rate"] == 0.03

    def test_standard_thresholds_exist(self):
        """Test standard thresholds are defined."""
        assert "standard" in LABEL_THRESHOLDS
        assert LABEL_THRESHOLDS["standard"]["min_views"] == 10_000
        assert LABEL_THRESHOLDS["standard"]["min_engagement_rate"] == 0.01
        assert LABEL_THRESHOLDS["standard"]["max_engagement_rate"] == 0.03

    def test_failed_thresholds_exist(self):
        """Test failed thresholds are defined."""
        assert "failed" in LABEL_THRESHOLDS
        assert LABEL_THRESHOLDS["failed"]["max_views"] == 10_000
