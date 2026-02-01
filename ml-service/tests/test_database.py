"""
Tests for the database module.
"""
import pytest
import sqlite3
import tempfile
import os
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.db.database import Database, Upload, UploadPerformance, UploadOutcome


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with test schema."""
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


class TestDatabaseConnection:
    """Tests for database connection handling."""

    def test_connect_sqlite(self, temp_db):
        """Test connecting to SQLite database."""
        db = Database(temp_db)
        db.connect()
        assert db._conn is not None
        db.close()
        assert db._conn is None

    def test_context_manager(self, temp_db):
        """Test database context manager."""
        with Database(temp_db) as db:
            assert db._conn is not None
        # Connection should be closed after context

    def test_postgres_not_implemented(self):
        """Test that PostgreSQL raises NotImplementedError."""
        db = Database("postgresql://user:pass@localhost:5432/test")
        with pytest.raises(NotImplementedError):
            db.connect()


class TestGetUploadsForTracking:
    """Tests for get_uploads_for_tracking method."""

    def test_returns_empty_when_no_uploads(self, temp_db):
        """Test returns empty list when no uploads exist."""
        with Database(temp_db) as db:
            uploads = db.get_uploads_for_tracking(24)
            assert uploads == []

    def test_returns_uploads_due_for_checkpoint(self, temp_db):
        """Test returns uploads that are due for a checkpoint."""
        # Insert upload from 25 hours ago (due for 24h checkpoint)
        conn = sqlite3.connect(temp_db)
        uploaded_at = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", uploaded_at)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_uploads_for_tracking(24)
            assert len(uploads) == 1
            assert uploads[0].video_id == "vid1"
            assert uploads[0].bilibili_bvid == "BV1test123"

    def test_excludes_already_tracked(self, temp_db):
        """Test excludes uploads already tracked at checkpoint."""
        conn = sqlite3.connect(temp_db)
        uploaded_at = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", uploaded_at)
        )
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", 24, datetime.utcnow().isoformat(), 1000, 50, 20, 30, 10, 100, 5, 41.67, 0.01)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_uploads_for_tracking(24)
            assert len(uploads) == 0

    def test_excludes_uploads_without_bvid(self, temp_db):
        """Test excludes uploads without bilibili_bvid."""
        conn = sqlite3.connect(temp_db)
        uploaded_at = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", None, uploaded_at)
        )
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid2", "ch1", "", uploaded_at)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_uploads_for_tracking(24)
            assert len(uploads) == 0


class TestSavePerformance:
    """Tests for save_performance method."""

    def test_save_new_performance(self, temp_db):
        """Test saving new performance record."""
        # Insert upload first
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            perf = UploadPerformance(
                id=None,
                upload_id="vid1",
                checkpoint_hours=24,
                recorded_at=datetime.utcnow(),
                views=10000,
                likes=500,
                coins=200,
                favorites=300,
                shares=50,
                danmaku=100,
                comments=30,
                view_velocity=416.67,
                engagement_rate=0.10
            )
            db.save_performance(perf)

        # Verify saved
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM upload_performance WHERE upload_id = ?",
            ("vid1",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["views"] == 10000
        assert row["checkpoint_hours"] == 24
        assert row["engagement_rate"] == 0.10

    def test_upsert_performance(self, temp_db):
        """Test updating existing performance record."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", 24, datetime.utcnow().isoformat(), 1000, 50, 20, 30, 10, 100, 5, 41.67, 0.01)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            perf = UploadPerformance(
                id=None,
                upload_id="vid1",
                checkpoint_hours=24,
                recorded_at=datetime.utcnow(),
                views=2000,  # Updated
                likes=100,
                coins=40,
                favorites=60,
                shares=20,
                danmaku=200,
                comments=10,
                view_velocity=83.33,
                engagement_rate=0.10
            )
            db.save_performance(perf)

        # Verify updated
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM upload_performance WHERE upload_id = ?",
            ("vid1",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row["views"] == 2000


class TestSaveOutcome:
    """Tests for save_outcome method."""

    def test_save_new_outcome(self, temp_db):
        """Test saving new outcome record."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            outcome = UploadOutcome(
                id=None,
                upload_id="vid1",
                label="successful",
                labeled_at=datetime.utcnow(),
                final_views=500000,
                final_engagement_rate=0.04,
                final_coins=5000
            )
            db.save_outcome(outcome)

        # Verify saved
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM upload_outcomes WHERE upload_id = ?",
            ("vid1",)
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["label"] == "successful"
        assert row["final_views"] == 500000

    def test_save_all_label_types(self, temp_db):
        """Test saving all label types."""
        labels = ["viral", "successful", "standard", "failed"]

        conn = sqlite3.connect(temp_db)
        for i, label in enumerate(labels):
            conn.execute(
                "INSERT INTO uploads VALUES (?, ?, ?, ?)",
                (f"vid{i}", "ch1", f"BV1test{i}", datetime.utcnow().isoformat())
            )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            for i, label in enumerate(labels):
                outcome = UploadOutcome(
                    id=None,
                    upload_id=f"vid{i}",
                    label=label,
                    labeled_at=datetime.utcnow(),
                    final_views=100000 * (i + 1),
                    final_engagement_rate=0.01 * (i + 1),
                    final_coins=1000 * (i + 1)
                )
                db.save_outcome(outcome)

        # Verify all saved
        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT label FROM upload_outcomes")
        saved_labels = [row["label"] for row in cursor.fetchall()]
        conn.close()

        assert set(saved_labels) == set(labels)


class TestGetLatestPerformance:
    """Tests for get_latest_performance method."""

    def test_returns_none_when_no_data(self, temp_db):
        """Test returns None when no performance data exists."""
        with Database(temp_db) as db:
            perf = db.get_latest_performance("nonexistent")
            assert perf is None

    def test_returns_latest_checkpoint(self, temp_db):
        """Test returns the latest checkpoint performance."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        # Insert multiple checkpoints
        for cp, views in [(1, 100), (6, 600), (24, 2400), (48, 4800)]:
            conn.execute(
                "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (None, "vid1", cp, datetime.utcnow().isoformat(), views, 50, 20, 30, 10, 100, 5, views/cp, 0.01)
            )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            perf = db.get_latest_performance("vid1")
            assert perf is not None
            assert perf.checkpoint_hours == 48
            assert perf.views == 4800


class TestGetUploadsForLabeling:
    """Tests for get_uploads_for_labeling method."""

    def test_returns_uploads_with_required_checkpoint(self, temp_db):
        """Test returns uploads that have the required checkpoint."""
        conn = sqlite3.connect(temp_db)
        # Upload with 720h checkpoint (ready for labeling)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", 720, datetime.utcnow().isoformat(), 50000, 2500, 1000, 500, 200, 1500, 300, 69.44, 0.08)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_uploads_for_labeling(720)
            assert len(uploads) == 1
            assert uploads[0].video_id == "vid1"

    def test_excludes_already_labeled(self, temp_db):
        """Test excludes uploads that are already labeled."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO upload_performance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", 720, datetime.utcnow().isoformat(), 50000, 2500, 1000, 500, 200, 1500, 300, 69.44, 0.08)
        )
        conn.execute(
            "INSERT INTO upload_outcomes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (None, "vid1", "successful", datetime.utcnow().isoformat(), 50000, 0.08, 1000)
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_uploads_for_labeling(720)
            assert len(uploads) == 0


class TestGetAllUploadsWithBvid:
    """Tests for get_all_uploads_with_bvid method."""

    def test_returns_only_uploads_with_bvid(self, temp_db):
        """Test returns only uploads that have a bilibili_bvid."""
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid1", "ch1", "BV1test123", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid2", "ch1", None, datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid3", "ch1", "", datetime.utcnow().isoformat())
        )
        conn.execute(
            "INSERT INTO uploads VALUES (?, ?, ?, ?)",
            ("vid4", "ch1", "BV1test456", datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()

        with Database(temp_db) as db:
            uploads = db.get_all_uploads_with_bvid()
            assert len(uploads) == 2
            bvids = [u.bilibili_bvid for u in uploads]
            assert "BV1test123" in bvids
            assert "BV1test456" in bvids


class TestDatabaseErrors:
    """Tests for database error handling."""

    def test_operations_fail_without_connection(self, temp_db):
        """Test that operations fail when database is not connected."""
        db = Database(temp_db)
        # Don't call connect()

        with pytest.raises(RuntimeError, match="Database not connected"):
            db.get_uploads_for_tracking(24)

        with pytest.raises(RuntimeError, match="Database not connected"):
            db.save_performance(UploadPerformance(
                id=None, upload_id="test", checkpoint_hours=24,
                recorded_at=datetime.utcnow(), views=0, likes=0,
                coins=0, favorites=0, shares=0, danmaku=0,
                comments=0, view_velocity=0, engagement_rate=0
            ))

        with pytest.raises(RuntimeError, match="Database not connected"):
            db.save_outcome(UploadOutcome(
                id=None, upload_id="test", label="failed",
                labeled_at=datetime.utcnow(), final_views=0,
                final_engagement_rate=0, final_coins=0
            ))
