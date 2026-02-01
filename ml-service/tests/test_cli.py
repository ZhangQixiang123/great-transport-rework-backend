"""
Tests for the CLI module.
"""
import pytest
import sqlite3
import tempfile
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.cli import parse_args, cmd_track, cmd_label, cmd_stats
from app.db.database import Database


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with test schema and data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Create schema
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE uploads (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            bilibili_bvid TEXT,
            uploaded_at TEXT NOT NULL
        );

        CREATE TABLE upload_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id TEXT NOT NULL,
            checkpoint_hours INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            views INTEGER NOT NULL,
            likes INTEGER NOT NULL,
            coins INTEGER NOT NULL,
            favorites INTEGER NOT NULL,
            shares INTEGER NOT NULL,
            danmaku INTEGER NOT NULL,
            comments INTEGER NOT NULL,
            view_velocity REAL NOT NULL,
            engagement_rate REAL NOT NULL,
            UNIQUE(upload_id, checkpoint_hours)
        );

        CREATE TABLE upload_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id TEXT UNIQUE NOT NULL,
            label TEXT NOT NULL,
            labeled_at TEXT NOT NULL,
            final_views INTEGER NOT NULL,
            final_engagement_rate REAL NOT NULL,
            final_coins INTEGER NOT NULL
        );
    """)
    conn.close()

    yield db_path

    # Cleanup
    os.unlink(db_path)


class TestParseArgs:
    """Tests for argument parsing."""

    def test_track_command(self):
        """Test parsing track command."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', 'track']):
            args = parse_args()
            assert args.command == "track"
            assert args.db_path == "/test/db.sqlite"
            assert args.checkpoint is None

    def test_track_command_with_checkpoint(self):
        """Test parsing track command with specific checkpoint."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', 'track', '--checkpoint', '24']):
            args = parse_args()
            assert args.command == "track"
            assert args.checkpoint == 24

    def test_label_command(self):
        """Test parsing label command."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', 'label']):
            args = parse_args()
            assert args.command == "label"
            assert args.min_checkpoint == 168  # default

    def test_label_command_custom_min_checkpoint(self):
        """Test parsing label command with custom min checkpoint."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', 'label', '--min-checkpoint', '720']):
            args = parse_args()
            assert args.command == "label"
            assert args.min_checkpoint == 720

    def test_stats_command(self):
        """Test parsing stats command."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', 'stats']):
            args = parse_args()
            assert args.command == "stats"

    def test_json_output_flag(self):
        """Test parsing --json flag."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite', '--json', 'stats']):
            args = parse_args()
            assert args.json is True

    def test_missing_db_path_fails(self):
        """Test that missing --db-path raises error."""
        with patch.object(sys, 'argv', ['cli.py', 'stats']):
            with pytest.raises(SystemExit):
                parse_args()

    def test_missing_command_fails(self):
        """Test that missing command raises error."""
        with patch.object(sys, 'argv', ['cli.py', '--db-path', '/test/db.sqlite']):
            with pytest.raises(SystemExit):
                parse_args()


class TestCmdStats:
    """Tests for cmd_stats function."""

    def test_stats_empty_database(self, temp_db):
        """Test stats command with empty database."""
        with Database(temp_db) as db:
            args = MagicMock()
            result = cmd_stats(db, args)

            assert result["command"] == "stats"
            assert result["total_uploads_with_bvid"] == 0
            assert result["by_label"]["unlabeled"] == 0

    def test_stats_with_data(self, temp_db):
        """Test stats command with sample data."""
        conn = sqlite3.connect(temp_db)
        # Insert uploads
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid2", "ch1", "BV1test456", datetime.utcnow().isoformat())
        )
        # Insert performance data
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", 24, datetime.utcnow().isoformat(), 10000, 500, 200, 300, 50, 100, 30, 416.67, 0.10)
        )
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid2", 24, datetime.utcnow().isoformat(), 20000, 1000, 400, 600, 100, 200, 60, 833.33, 0.10)
        )
        # Insert outcome for vid1
        conn.execute(
            "INSERT INTO upload_outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", "standard", datetime.utcnow().isoformat(), 10000, 0.10, 200)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            args = MagicMock()
            result = cmd_stats(db, args)

            assert result["command"] == "stats"
            assert result["total_uploads_with_bvid"] == 2
            assert result["by_label"]["standard"] == 1
            assert result["by_label"]["unlabeled"] == 1
            # Check averages
            assert result["averages"]["views"] == 15000.0  # (10000 + 20000) / 2

    def test_stats_all_labels(self, temp_db):
        """Test stats correctly counts all label types."""
        conn = sqlite3.connect(temp_db)
        labels = ["viral", "successful", "standard", "failed"]
        for i, label in enumerate(labels):
            vid = f"vid{i}"
            conn.execute(
                "INSERT INTO uploads VALUES (?, ?, ?, ?)",
                (vid, "ch1", f"BV1test{i}", datetime.utcnow().isoformat())
            )
            conn.execute(
                "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (None, vid, 168, datetime.utcnow().isoformat(), 1000, 50, 20, 30, 10, 100, 5, 5.95, 0.01)
            )
            conn.execute(
                "INSERT INTO upload_outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (None, vid, label, datetime.utcnow().isoformat(), 1000, 0.01, 20)
            )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            args = MagicMock()
            result = cmd_stats(db, args)

            assert result["by_label"]["viral"] == 1
            assert result["by_label"]["successful"] == 1
            assert result["by_label"]["standard"] == 1
            assert result["by_label"]["failed"] == 1
            assert result["by_label"]["unlabeled"] == 0


class TestCmdTrack:
    """Tests for cmd_track function."""

    @pytest.mark.asyncio
    async def test_track_specific_checkpoint_no_uploads(self, temp_db):
        """Test track command with specific checkpoint and no uploads."""
        with Database(temp_db) as db:
            args = MagicMock()
            args.checkpoint = 24
            result = await cmd_track(db, args)

            assert result["command"] == "track"
            assert result["checkpoint"] == 24
            assert result["tracked"] == 0

    @pytest.mark.asyncio
    async def test_track_all_checkpoints_returns_dict(self, temp_db):
        """Test track all command returns checkpoint dict."""
        with Database(temp_db) as db:
            with patch('app.cli.BilibiliTracker') as mock_tracker_class:
                mock_tracker = MagicMock()
                mock_tracker.track_all_due = AsyncMock(return_value={1: 0, 6: 0, 24: 0, 48: 0, 168: 0, 720: 0})
                mock_tracker_class.return_value = mock_tracker

                args = MagicMock()
                args.checkpoint = None
                result = await cmd_track(db, args)

                assert result["command"] == "track"
                assert "by_checkpoint" in result
                assert result["total_tracked"] == 0


class TestCmdLabel:
    """Tests for cmd_label function."""

    @pytest.mark.asyncio
    async def test_label_no_uploads(self, temp_db):
        """Test label command with no uploads to label."""
        with Database(temp_db) as db:
            with patch('app.cli.BilibiliTracker') as mock_tracker_class:
                mock_tracker = MagicMock()
                mock_tracker.label_all_due = AsyncMock(return_value=0)
                mock_tracker_class.return_value = mock_tracker

                args = MagicMock()
                args.min_checkpoint = 168
                result = await cmd_label(db, args)

                assert result["command"] == "label"
                assert result["min_checkpoint"] == 168
                assert result["labeled"] == 0

    @pytest.mark.asyncio
    async def test_label_with_custom_min_checkpoint(self, temp_db):
        """Test label command uses custom min_checkpoint."""
        with Database(temp_db) as db:
            with patch('app.cli.BilibiliTracker') as mock_tracker_class:
                mock_tracker = MagicMock()
                mock_tracker.label_all_due = AsyncMock(return_value=5)
                mock_tracker_class.return_value = mock_tracker

                args = MagicMock()
                args.min_checkpoint = 720
                result = await cmd_label(db, args)

                mock_tracker.label_all_due.assert_called_once_with(720)
                assert result["labeled"] == 5


class TestResultFormat:
    """Tests for result formatting consistency."""

    def test_track_result_has_required_keys(self, temp_db):
        """Test track result has all required keys."""
        with Database(temp_db) as db:
            args = MagicMock()
            args.checkpoint = 24

            import asyncio
            result = asyncio.get_event_loop().run_until_complete(cmd_track(db, args))

            assert "command" in result
            assert result["command"] == "track"

    def test_stats_result_has_required_keys(self, temp_db):
        """Test stats result has all required keys."""
        with Database(temp_db) as db:
            args = MagicMock()
            result = cmd_stats(db, args)

            required_keys = ["command", "total_uploads_with_bvid", "by_label", "averages"]
            for key in required_keys:
                assert key in result

            avg_keys = ["views", "likes", "coins", "engagement_rate"]
            for key in avg_keys:
                assert key in result["averages"]
