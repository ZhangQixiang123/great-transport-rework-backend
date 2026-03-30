import os
import sys
import json
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.bootstrap import (
    run_bootstrap,
    _seed_strategies,
    _bootstrap_scoring,
    _seed_followed_channels,
    refresh_strategies,
    INITIAL_STRATEGIES,
)


@pytest.fixture
def db():
    """Create a real in-memory Database with skill tables initialized."""
    database = Database(":memory:")
    database.connect()
    database.ensure_skill_tables()
    yield database
    database.close()


EXPECTED_STRATEGY_NAMES = [
    "gaming_deep_dive",
    "educational_explainer",
    "tech_teardown",
    "chinese_brand_foreign_review",
    "social_commentary",
    "geopolitics_hot_take",
    "challenge_experiment",
    "global_trending_chinese_angle",
    "surveillance_dashcam",
]


class TestRunBootstrap:
    """Tests for run_bootstrap()."""

    def test_bootstrap_with_skip_llm(self, db):
        """run_bootstrap with skip_llm=True should seed strategies and scoring."""
        result = run_bootstrap(db, backend=None, skip_llm=True)
        assert result["strategies_seeded"] == 9
        assert result["llm_principles"] is False
        assert isinstance(result["scoring_bootstrapped"], bool)
        assert isinstance(result["channels_seeded"], int)

    def test_bootstrap_returns_dict(self, db):
        """run_bootstrap should return a dict with expected keys."""
        result = run_bootstrap(db, backend=None, skip_llm=True)
        assert "strategies_seeded" in result
        assert "channels_seeded" in result
        assert "scoring_bootstrapped" in result
        assert "llm_principles" in result

    def test_bootstrap_idempotent(self, db):
        """Running bootstrap twice should not re-seed strategies."""
        result1 = run_bootstrap(db, backend=None, skip_llm=True)
        result2 = run_bootstrap(db, backend=None, skip_llm=True)
        assert result1["strategies_seeded"] == 9
        assert result2["strategies_seeded"] == 0

    def test_bootstrap_ensures_skill_tables(self, db):
        """run_bootstrap should call ensure_skill_tables (tables should exist after)."""
        # Tables already created by fixture, but bootstrap should not fail
        run_bootstrap(db, backend=None, skip_llm=True)
        # Verify tables exist by querying them
        row = db._conn.execute("SELECT COUNT(*) as cnt FROM strategies").fetchone()
        assert row["cnt"] == 9

    def test_bootstrap_with_none_backend_and_no_skip(self, db):
        """With backend=None and skip_llm=False, LLM principles should be skipped."""
        result = run_bootstrap(db, backend=None, skip_llm=False)
        # backend is None, so even though skip_llm is False, it won't call LLM
        assert result["llm_principles"] is False


class TestSeedStrategies:
    """Tests for _seed_strategies()."""

    def test_seeds_8_strategies(self, db):
        """_seed_strategies should create exactly 8 strategies."""
        count = _seed_strategies(db)
        assert count == 9

    def test_strategy_names_match_expected(self, db):
        """All 8 expected strategy names should be present."""
        _seed_strategies(db)
        strategies = db.list_strategies(active_only=True)
        names = [s["name"] for s in strategies]
        for name in EXPECTED_STRATEGY_NAMES:
            assert name in names, f"Strategy '{name}' not found"

    def test_idempotent_seeding(self, db):
        """Calling _seed_strategies twice should not duplicate strategies."""
        count1 = _seed_strategies(db)
        count2 = _seed_strategies(db)
        assert count1 == 9
        assert count2 == 0
        strategies = db.list_strategies(active_only=True)
        assert len(strategies) == 9

    def test_strategies_have_required_fields(self, db):
        """Each seeded strategy should have description, source, and bilibili_check."""
        _seed_strategies(db)
        for name in EXPECTED_STRATEGY_NAMES:
            s = db.get_strategy(name)
            assert s is not None, f"Strategy '{name}' not found"
            assert s["description"], f"Strategy '{name}' has empty description"
            assert s["source"] == "bootstrap"

    def test_strategies_have_example_queries(self, db):
        """Each seeded strategy should have valid JSON example_queries."""
        _seed_strategies(db)
        for name in EXPECTED_STRATEGY_NAMES:
            s = db.get_strategy(name)
            queries = s.get("example_queries")
            if queries:
                parsed = json.loads(queries)
                assert isinstance(parsed, list)

    def test_initial_strategies_constant_has_8(self):
        """INITIAL_STRATEGIES constant should have exactly 8 entries."""
        assert len(INITIAL_STRATEGIES) == 9
        names = [s["name"] for s in INITIAL_STRATEGIES]
        assert sorted(names) == sorted(EXPECTED_STRATEGY_NAMES)


class TestRefreshStrategies:
    """Tests for refresh_strategies()."""

    def test_refresh_updates_example_queries(self, db):
        """refresh_strategies should update example_queries for existing strategies."""
        _seed_strategies(db)
        # Manually overwrite one strategy's queries to simulate old DB state
        db.update_strategy_metadata(
            "gaming_deep_dive",
            example_queries='["old placeholder query 2026"]',
        )
        # Verify it's old
        s = db.get_strategy("gaming_deep_dive")
        assert "old placeholder" in s["example_queries"]

        updated = refresh_strategies(db)
        assert updated >= 1
        s = db.get_strategy("gaming_deep_dive")
        assert "old placeholder" not in s["example_queries"]
        assert "Starfield" in s["example_queries"]

    def test_refresh_skips_unknown_strategies(self, db):
        """refresh_strategies should not create strategies not in DB."""
        # Only seed a few strategies manually
        db.add_strategy(name="gaming_deep_dive", description="test",
                        example_queries='["old"]', source="test")
        updated = refresh_strategies(db)
        assert updated == 1
        # Verify other INITIAL_STRATEGIES were NOT created
        assert db.get_strategy("educational_explainer") is None

    def test_refresh_idempotent(self, db):
        """Running refresh twice with same data should update 0 the second time."""
        _seed_strategies(db)
        updated1 = refresh_strategies(db)
        updated2 = refresh_strategies(db)
        assert updated2 == 0


class TestBootstrapScoring:
    """Tests for _bootstrap_scoring()."""

    def test_scoring_without_data_saves_defaults(self, db):
        """_bootstrap_scoring without competitor data should save default params."""
        # No competitor_videos or youtube_stats tables, so scoring should fall back
        result = _bootstrap_scoring(db)
        # Should have saved scoring params (either from data or defaults)
        params_row = db.get_scoring_params()
        assert params_row is not None
        params = json.loads(params_row["params_json"])
        assert "bilibili_success_threshold" in params

    def test_scoring_with_competitor_data(self, db):
        """_bootstrap_scoring with competitor data should derive params from it."""
        db.ensure_competitor_tables()
        # Create youtube_stats table and insert data
        db._conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_stats (
                youtube_id TEXT PRIMARY KEY,
                yt_views INTEGER,
                yt_likes INTEGER,
                yt_duration_seconds INTEGER,
                yt_category_id INTEGER,
                yt_channel_title TEXT
            )
        """)
        # Insert some competitor videos with youtube stats
        for i in range(20):
            yt_id = f"yt_{i}"
            db._conn.execute(
                "INSERT INTO competitor_videos (bvid, bilibili_uid, title, views, youtube_source_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"BV{i}", "uid1", f"Video {i}", (i + 1) * 10000, yt_id),
            )
            db._conn.execute(
                "INSERT INTO youtube_stats (youtube_id, yt_views, yt_likes, yt_duration_seconds, yt_category_id, yt_channel_title) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (yt_id, (i + 1) * 50000, (i + 1) * 2000, 600, 22, "TestChannel"),
            )
        db._conn.commit()

        result = _bootstrap_scoring(db)
        assert result is True
        params_row = db.get_scoring_params()
        assert params_row is not None
        params = json.loads(params_row["params_json"])
        assert params["bilibili_success_threshold"] > 0


class TestSeedFollowedChannels:
    """Tests for _seed_followed_channels()."""

    def test_no_youtube_stats_table(self, db):
        """Without youtube_stats table, should return 0 channels."""
        db.ensure_competitor_tables()
        count = _seed_followed_channels(db)
        assert count == 0

    def test_with_youtube_stats_no_qualifying_channels(self, db):
        """With youtube_stats but no channels meeting threshold, returns 0."""
        db.ensure_competitor_tables()
        db._conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_stats (
                youtube_id TEXT PRIMARY KEY,
                yt_views INTEGER,
                yt_likes INTEGER,
                yt_duration_seconds INTEGER,
                yt_category_id INTEGER,
                yt_channel_title TEXT
            )
        """)
        # Insert videos from a channel with only 2 transports (below 3 threshold)
        for i in range(2):
            db._conn.execute(
                "INSERT INTO competitor_videos (bvid, bilibili_uid, title, views, youtube_source_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"BV_few_{i}", "uid1", f"Video {i}", 10000, f"yt_few_{i}"),
            )
            db._conn.execute(
                "INSERT INTO youtube_stats (youtube_id, yt_views, yt_likes, yt_duration_seconds, yt_category_id, yt_channel_title) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"yt_few_{i}", 50000, 2000, 600, 22, "SmallChannel"),
            )
        db._conn.commit()

        count = _seed_followed_channels(db)
        assert count == 0

    def test_with_qualifying_channels(self, db):
        """Channels with >= 3 transports should be seeded as followed."""
        db.ensure_competitor_tables()
        db._conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_stats (
                youtube_id TEXT PRIMARY KEY,
                yt_views INTEGER,
                yt_likes INTEGER,
                yt_duration_seconds INTEGER,
                yt_category_id INTEGER,
                yt_channel_title TEXT
            )
        """)
        # Insert 4 videos from the same channel
        for i in range(4):
            db._conn.execute(
                "INSERT INTO competitor_videos (bvid, bilibili_uid, title, views, youtube_source_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"BV_q_{i}", "uid1", f"Video {i}", 10000, f"yt_q_{i}"),
            )
            db._conn.execute(
                "INSERT INTO youtube_stats (youtube_id, yt_views, yt_likes, yt_duration_seconds, yt_category_id, yt_channel_title) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"yt_q_{i}", 50000, 2000, 600, 22, "PopularChannel"),
            )
        db._conn.commit()

        count = _seed_followed_channels(db)
        assert count == 1
        channels = db.list_followed_channels(active_only=True)
        assert len(channels) == 1
        assert channels[0]["channel_name"] == "PopularChannel"

    def test_with_disconnected_db(self):
        """Should return 0 when database is not connected."""
        database = Database(":memory:")
        # Don't connect
        count = _seed_followed_channels(database)
        assert count == 0

    def test_multiple_qualifying_channels(self, db):
        """Multiple channels meeting threshold should all be seeded."""
        db.ensure_competitor_tables()
        db._conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_stats (
                youtube_id TEXT PRIMARY KEY,
                yt_views INTEGER,
                yt_likes INTEGER,
                yt_duration_seconds INTEGER,
                yt_category_id INTEGER,
                yt_channel_title TEXT
            )
        """)
        # Insert videos from two channels, each with >= 3 transports
        for ch_idx, ch_name in enumerate(["ChannelA", "ChannelB"]):
            for i in range(3):
                yt_id = f"yt_{ch_idx}_{i}"
                db._conn.execute(
                    "INSERT INTO competitor_videos (bvid, bilibili_uid, title, views, youtube_source_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"BV_{ch_idx}_{i}", "uid1", f"Video {i}", 10000, yt_id),
                )
                db._conn.execute(
                    "INSERT INTO youtube_stats (youtube_id, yt_views, yt_likes, yt_duration_seconds, yt_category_id, yt_channel_title) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (yt_id, 50000, 2000, 600, 22, ch_name),
                )
        db._conn.commit()

        count = _seed_followed_channels(db)
        assert count == 2
