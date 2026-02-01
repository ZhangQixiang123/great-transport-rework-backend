"""
Tests for CompetitorMonitor module.
"""
import pytest
from datetime import datetime

from app.collectors.competitor_monitor import (
    extract_youtube_source_id,
    CompetitorMonitor,
)
from app.db.database import Database, CompetitorChannel, CompetitorVideo


class TestExtractYoutubeSourceId:
    """Tests for YouTube source ID extraction."""

    def test_extract_from_brackets(self):
        """Test extracting ID from [VIDEO_ID] format."""
        title = "Amazing Video [dQw4w9WgXcQ]"
        description = "Some description"
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"

    def test_extract_from_source_format(self):
        """Test extracting ID from (source: VIDEO_ID) format."""
        title = "Amazing Video"
        description = "Original content (source: dQw4w9WgXcQ)"
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"

    def test_extract_from_youtube_url(self):
        """Test extracting ID from youtube.com URL."""
        title = "Amazing Video"
        description = "Original: https://youtube.com/watch?v=dQw4w9WgXcQ"
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"

    def test_extract_from_youtu_be_url(self):
        """Test extracting ID from youtu.be URL."""
        title = "Amazing Video"
        description = "Source: https://youtu.be/dQw4w9WgXcQ"
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"

    def test_extract_from_yt_prefix(self):
        """Test extracting ID from yt: format."""
        title = "yt: dQw4w9WgXcQ - Amazing Video"
        description = ""
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"

    def test_no_match_returns_none(self):
        """Test that no match returns None."""
        title = "Just a regular video title"
        description = "No YouTube source here"
        result = extract_youtube_source_id(title, description)
        assert result is None

    def test_invalid_id_length_returns_none(self):
        """Test that invalid ID lengths are rejected."""
        title = "Short ID [abc123]"
        description = ""
        result = extract_youtube_source_id(title, description)
        assert result is None

    def test_case_insensitive(self):
        """Test that extraction is case insensitive."""
        title = "Video"
        description = "YOUTUBE: dQw4w9WgXcQ"
        result = extract_youtube_source_id(title, description)
        assert result == "dQw4w9WgXcQ"


class TestCompetitorMonitorDatabase:
    """Tests for CompetitorMonitor with database operations."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a test database."""
        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.connect()
        db.ensure_competitor_tables()
        yield db
        db.close()

    def test_add_and_list_competitor_channels(self, db):
        """Test adding and listing competitor channels."""
        channel = CompetitorChannel(
            bilibili_uid="12345",
            name="Test Channel",
            description="A test transporter",
            follower_count=50000,
            video_count=100,
            added_at=datetime.utcnow(),
            is_active=True
        )
        db.add_competitor_channel(channel)

        channels = db.list_competitor_channels()
        assert len(channels) == 1
        assert channels[0].bilibili_uid == "12345"
        assert channels[0].name == "Test Channel"
        assert channels[0].follower_count == 50000

    def test_deactivate_competitor_channel(self, db):
        """Test deactivating a competitor channel."""
        channel = CompetitorChannel(
            bilibili_uid="12345",
            name="Test Channel",
            description="",
            follower_count=1000,
            video_count=10,
            added_at=datetime.utcnow(),
            is_active=True
        )
        db.add_competitor_channel(channel)

        # Deactivate
        db.deactivate_competitor_channel("12345")

        # Should not appear in active list
        channels = db.list_competitor_channels(active_only=True)
        assert len(channels) == 0

    def test_save_and_get_competitor_videos(self, db):
        """Test saving and retrieving competitor videos."""
        # First add a channel
        channel = CompetitorChannel(
            bilibili_uid="12345",
            name="Test Channel",
            description="",
            follower_count=1000,
            video_count=10,
            added_at=datetime.utcnow(),
            is_active=True
        )
        db.add_competitor_channel(channel)

        # Add a video
        video = CompetitorVideo(
            bvid="BV1234567890",
            bilibili_uid="12345",
            title="Test Video",
            description="A test video",
            duration=300,
            views=100000,
            likes=5000,
            coins=1000,
            favorites=500,
            shares=200,
            danmaku=1000,
            comments=300,
            publish_time=datetime.utcnow(),
            collected_at=datetime.utcnow(),
            youtube_source_id="dQw4w9WgXcQ",
            label=None
        )
        db.save_competitor_video(video)

        # Retrieve
        videos = db.get_competitor_videos(uid="12345")
        assert len(videos) == 1
        assert videos[0].bvid == "BV1234567890"
        assert videos[0].views == 100000
        assert videos[0].youtube_source_id == "dQw4w9WgXcQ"

    def test_update_competitor_video_label(self, db):
        """Test updating video labels."""
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
            bvid="BV1234567890",
            bilibili_uid="12345",
            title="Test Video",
            description="",
            duration=0,
            views=50000,
            likes=2000,
            coins=500,
            favorites=300,
            shares=100,
            danmaku=500,
            comments=100,
            publish_time=None,
            collected_at=datetime.utcnow(),
            youtube_source_id=None,
            label=None
        )
        db.save_competitor_video(video)

        # Update label
        db.update_competitor_video_label("BV1234567890", "successful")

        # Verify
        videos = db.get_competitor_videos(label="successful")
        assert len(videos) == 1
        assert videos[0].label == "successful"

    def test_get_training_data_summary(self, db):
        """Test getting training data summary."""
        # Empty summary
        summary = db.get_training_data_summary()
        assert summary["total"] == 0
        assert summary["viral"] == 0
        assert summary["unlabeled"] == 0

    def test_get_unlabeled_videos(self, db):
        """Test getting unlabeled videos."""
        # Add channel and unlabeled video
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
            bvid="BV1234567890",
            bilibili_uid="12345",
            title="Unlabeled Video",
            description="",
            duration=0,
            views=10000,
            likes=500,
            coins=100,
            favorites=50,
            shares=10,
            danmaku=100,
            comments=20,
            publish_time=None,
            collected_at=datetime.utcnow(),
            youtube_source_id=None,
            label=None  # Unlabeled
        )
        db.save_competitor_video(video)

        unlabeled = db.get_unlabeled_competitor_videos()
        assert len(unlabeled) == 1
        assert unlabeled[0].bvid == "BV1234567890"
