import os
import sys
import json
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.outcomes.tracker import OutcomeTracker


@pytest.fixture
def db():
    """Create a real in-memory Database with skill tables initialized."""
    database = Database(":memory:")
    database.connect()
    database.ensure_skill_tables()
    yield database
    database.close()


@pytest.fixture
def tracker(db):
    """Create an OutcomeTracker with the in-memory database."""
    return OutcomeTracker(db)


def _seed_strategy(db, name="test_strategy", description="Test"):
    """Helper to insert a strategy and return its id."""
    return db.add_strategy(name=name, description=description, source="test")


def _seed_run(db, strategy_id, query="test query"):
    """Helper to insert a strategy run and return its id."""
    return db.save_strategy_run(strategy_id, query)


class TestRecordQueryYield:
    """Tests for record_query_yield()."""

    def test_record_yield_success(self, tracker, db):
        """Recording yield with a best video should set yield_success=1."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)

        best_video = {
            "id": "abc123",
            "title": "Great Video",
            "channel": "TestChannel",
            "views": 100000,
            "likes": 5000,
            "category_id": 22,
            "duration_seconds": 600,
        }
        tracker.record_query_yield(run_id, result_count=10, avg_views=50000, best_video=best_video)

        row = db._conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["query_result_count"] == 10
        assert row["query_avg_views"] == 50000
        assert row["yield_success"] == 1
        assert row["youtube_video_id"] == "abc123"
        assert row["youtube_title"] == "Great Video"
        assert row["youtube_channel"] == "TestChannel"
        assert row["youtube_views"] == 100000
        assert row["youtube_likes"] == 5000
        assert row["youtube_category_id"] == 22
        assert row["youtube_duration_seconds"] == 600

    def test_record_yield_failure(self, tracker, db):
        """Recording yield with no best video should set yield_success=0."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)

        tracker.record_query_yield(run_id, result_count=0, avg_views=0, best_video=None)

        row = db._conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["query_result_count"] == 0
        assert row["yield_success"] == 0
        assert row["youtube_video_id"] is None

    def test_record_yield_with_results_but_no_best(self, tracker, db):
        """Having results but no best_video should still set yield_success=0."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)

        tracker.record_query_yield(run_id, result_count=5, avg_views=10000, best_video=None)

        row = db._conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["query_result_count"] == 5
        assert row["yield_success"] == 0

    def test_record_multiple_runs(self, tracker, db):
        """Multiple runs should each have their own yield data."""
        sid = _seed_strategy(db)
        run1 = _seed_run(db, sid, "query 1")
        run2 = _seed_run(db, sid, "query 2")

        tracker.record_query_yield(run1, 10, 50000, {"id": "v1", "title": "V1",
                                                       "channel": "C1", "views": 100000})
        tracker.record_query_yield(run2, 0, 0, None)

        r1 = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run1,)).fetchone()
        r2 = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run2,)).fetchone()
        assert r1["yield_success"] == 1
        assert r2["yield_success"] == 0


class TestUpdateStrategyYieldStats:
    """Tests for update_strategy_yield_stats()."""

    def test_recomputes_yield_stats(self, tracker, db):
        """update_strategy_yield_stats should recompute from strategy_runs."""
        sid = _seed_strategy(db)

        # Create 3 runs: 2 successful, 1 failed
        for i in range(3):
            run_id = _seed_run(db, sid, f"query {i}")
            best = {"id": f"v{i}", "title": f"V{i}", "channel": "C", "views": 100000} if i < 2 else None
            tracker.record_query_yield(run_id, 10 if i < 2 else 0, 50000 if i < 2 else 0, best)

        tracker.update_strategy_yield_stats(sid)

        strategy = db.get_strategy("test_strategy")
        assert strategy["total_queries"] == 3
        assert strategy["yielded_queries"] == 2

    def test_stats_with_no_runs(self, tracker, db):
        """update_strategy_yield_stats with no runs should set 0/0."""
        sid = _seed_strategy(db)
        tracker.update_strategy_yield_stats(sid)

        strategy = db.get_strategy("test_strategy")
        assert strategy["total_queries"] == 0
        assert strategy["yielded_queries"] == 0

    def test_stats_all_successful(self, tracker, db):
        """When all runs are successful, yielded should equal total."""
        sid = _seed_strategy(db)
        for i in range(5):
            run_id = _seed_run(db, sid, f"query {i}")
            tracker.record_query_yield(run_id, 10, 50000,
                                        {"id": f"v{i}", "title": f"V{i}", "channel": "C", "views": 100000})

        tracker.update_strategy_yield_stats(sid)

        strategy = db.get_strategy("test_strategy")
        assert strategy["total_queries"] == 5
        assert strategy["yielded_queries"] == 5

    def test_raises_when_not_connected(self, db):
        """Should raise RuntimeError if database is not connected."""
        db.close()
        tracker = OutcomeTracker(db)
        with pytest.raises(RuntimeError, match="not connected"):
            tracker.update_strategy_yield_stats(1)


class TestMarkTransported:
    """Tests for mark_transported()."""

    def test_mark_transported_updates_run(self, tracker, db):
        """mark_transported should find the run by youtube_video_id and update it."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)
        tracker.record_query_yield(run_id, 10, 50000,
                                    {"id": "yt123", "title": "V", "channel": "C", "views": 100000})

        tracker.mark_transported("yt123", "BV1abc123")

        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["was_transported"] == 1
        assert row["bilibili_bvid"] == "BV1abc123"

    def test_mark_transported_nonexistent_video(self, tracker, db):
        """mark_transported with nonexistent youtube_video_id should not fail."""
        tracker.mark_transported("nonexistent_id", "BV1xxx")
        # Should not raise any exception

    def test_mark_transported_picks_latest_run(self, tracker, db):
        """When multiple runs have the same video, should pick the latest."""
        sid = _seed_strategy(db)
        run1 = _seed_run(db, sid, "query1")
        tracker.record_query_yield(run1, 10, 50000,
                                    {"id": "yt123", "title": "V", "channel": "C", "views": 100000})
        run2 = _seed_run(db, sid, "query2")
        tracker.record_query_yield(run2, 10, 50000,
                                    {"id": "yt123", "title": "V", "channel": "C", "views": 100000})

        tracker.mark_transported("yt123", "BV1abc")

        # The latest run (run2) should be marked
        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run2,)).fetchone()
        assert row["was_transported"] == 1


class TestUpdateBilibiliViews:
    """Tests for update_bilibili_views()."""

    def test_success_above_threshold(self, tracker, db):
        """Views >= threshold should mark outcome as 'success'."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)
        tracker.record_query_yield(run_id, 10, 50000,
                                    {"id": "yt1", "title": "V", "channel": "C", "views": 100000})
        tracker.mark_transported("yt1", "BV1test")

        tracker.update_bilibili_views("BV1test", 80000)

        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["bilibili_views"] == 80000
        assert row["outcome"] == "success"
        assert row["outcome_recorded_at"] is not None

    def test_failure_below_threshold(self, tracker, db):
        """Views < threshold should mark outcome as 'failure'."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)
        tracker.record_query_yield(run_id, 10, 50000,
                                    {"id": "yt2", "title": "V", "channel": "C", "views": 100000})
        tracker.mark_transported("yt2", "BV1fail")

        tracker.update_bilibili_views("BV1fail", 10000)

        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["bilibili_views"] == 10000
        assert row["outcome"] == "failure"

    def test_uses_default_threshold_when_no_scoring_params(self, tracker, db):
        """Without scoring_params, should use default threshold of 50,000."""
        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)
        tracker.record_query_yield(run_id, 10, 50000,
                                    {"id": "yt3", "title": "V", "channel": "C", "views": 100000})
        tracker.mark_transported("yt3", "BV1default")

        # 49999 < 50000 => failure
        tracker.update_bilibili_views("BV1default", 49999)
        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["outcome"] == "failure"

        # Reset and test at threshold
        db._conn.execute("UPDATE strategy_runs SET outcome = NULL, bilibili_views = NULL WHERE id = ?", (run_id,))
        db._conn.commit()

        tracker.update_bilibili_views("BV1default", 50000)
        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["outcome"] == "success"

    def test_uses_custom_threshold_from_scoring_params(self, tracker, db):
        """Should use threshold from scoring_params when available."""
        from app.scoring.heuristic import ScoringParams
        params = ScoringParams(bilibili_success_threshold=20000)
        db.save_scoring_params(params.to_json(), source="test")

        sid = _seed_strategy(db)
        run_id = _seed_run(db, sid)
        tracker.record_query_yield(run_id, 10, 50000,
                                    {"id": "yt4", "title": "V", "channel": "C", "views": 100000})
        tracker.mark_transported("yt4", "BV1custom")

        # 25000 >= 20000 => success
        tracker.update_bilibili_views("BV1custom", 25000)
        row = db._conn.execute("SELECT * FROM strategy_runs WHERE id = ?", (run_id,)).fetchone()
        assert row["outcome"] == "success"

    def test_update_nonexistent_bvid(self, tracker, db):
        """update_bilibili_views with nonexistent bvid should not fail."""
        tracker.update_bilibili_views("BV1nonexistent", 100000)
        # Should not raise any exception
