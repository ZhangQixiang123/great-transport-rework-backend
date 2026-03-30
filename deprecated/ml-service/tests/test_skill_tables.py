"""
Tests for skill framework tables and CRUD methods in Database.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Create an in-memory SQLite database with skill tables."""
    database = Database(":memory:")
    database.connect()
    database.ensure_skill_tables()
    yield database
    database.close()


# ── Table Creation ────────────────────────────────────────────────────


class TestEnsureSkillTables:
    def test_creates_tables_without_error(self):
        """ensure_skill_tables() succeeds on a fresh connection."""
        database = Database(":memory:")
        database.connect()
        database.ensure_skill_tables()
        # Verify tables exist by querying sqlite_master
        tables = [
            r["name"]
            for r in database._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        for expected in [
            "skills", "skill_versions", "strategies",
            "strategy_runs", "followed_channels", "scoring_params",
        ]:
            assert expected in tables, f"Table '{expected}' not created"
        database.close()

    def test_idempotent(self, db):
        """Calling ensure_skill_tables() twice does not raise."""
        db.ensure_skill_tables()
        db.ensure_skill_tables()

    def test_raises_when_not_connected(self):
        """ensure_skill_tables() raises RuntimeError when not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError, match="Database not connected"):
            database.ensure_skill_tables()


# ── Skill CRUD ────────────────────────────────────────────────────────


class TestSkillCRUD:
    def test_upsert_skill_new(self, db):
        """upsert_skill inserts a new skill and returns its id."""
        skill_id = db.upsert_skill(
            "test_skill", "You are helpful.", "Prompt: {input}",
            json.dumps({"type": "object"}),
        )
        assert isinstance(skill_id, int)
        assert skill_id > 0

    def test_get_skill_returns_dict(self, db):
        """get_skill returns a dict with expected keys."""
        db.upsert_skill(
            "test_skill", "sys prompt", "tmpl {input}",
            json.dumps({"type": "object"}),
        )
        skill = db.get_skill("test_skill")
        assert skill is not None
        assert skill["name"] == "test_skill"
        assert skill["system_prompt"] == "sys prompt"
        assert skill["prompt_template"] == "tmpl {input}"
        assert skill["version"] == 1

    def test_get_skill_not_found(self, db):
        """get_skill returns None for a nonexistent skill."""
        assert db.get_skill("nonexistent") is None

    def test_upsert_skill_updates_existing(self, db):
        """upsert_skill updates prompts when the skill already exists."""
        db.upsert_skill("s1", "old sys", "old tmpl", '{"type":"object"}')
        db.upsert_skill("s1", "new sys", "new tmpl", '{"type":"object"}')
        skill = db.get_skill("s1")
        assert skill["system_prompt"] == "new sys"
        assert skill["prompt_template"] == "new tmpl"

    def test_update_skill_prompt_increments_version(self, db):
        """update_skill_prompt increments the version counter."""
        db.upsert_skill("s1", "sys", "tmpl", '{"type":"object"}')
        skill_before = db.get_skill("s1")
        assert skill_before["version"] == 1

        db.update_skill_prompt("s1", "new sys", "new tmpl")
        skill_after = db.get_skill("s1")
        assert skill_after["version"] == 2
        assert skill_after["system_prompt"] == "new sys"
        assert skill_after["prompt_template"] == "new tmpl"

    def test_snapshot_skill_version(self, db):
        """snapshot_skill_version saves a copy of the current prompts."""
        db.upsert_skill("s1", "sys v1", "tmpl v1", '{"type":"object"}')
        db.snapshot_skill_version("s1", "test_user", "testing snapshot")

        versions = db.get_skill_versions("s1")
        assert len(versions) == 1
        assert versions[0]["system_prompt"] == "sys v1"
        assert versions[0]["prompt_template"] == "tmpl v1"
        assert versions[0]["changed_by"] == "test_user"
        assert versions[0]["change_reason"] == "testing snapshot"

    def test_get_skill_versions_empty_for_unknown(self, db):
        """get_skill_versions returns [] for a nonexistent skill."""
        assert db.get_skill_versions("nonexistent") == []

    def test_snapshot_preserves_performance_before(self, db):
        """snapshot_skill_version stores performance_before metadata."""
        db.upsert_skill("s1", "sys", "tmpl", '{}')
        db.snapshot_skill_version("s1", "auto", "reflect", performance_before='{"yield_rate": 0.3}')
        versions = db.get_skill_versions("s1")
        assert versions[0]["performance_before"] == '{"yield_rate": 0.3}'

    def test_snapshot_noop_for_unknown_skill(self, db):
        """snapshot_skill_version silently returns if the skill doesn't exist."""
        # Should not raise
        db.snapshot_skill_version("nonexistent", "test", "reason")
        assert db.get_skill_versions("nonexistent") == []

    def test_skill_crud_not_connected(self):
        """Skill CRUD methods raise RuntimeError when DB not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError):
            database.get_skill("x")
        with pytest.raises(RuntimeError):
            database.upsert_skill("x", "s", "t", "{}")
        with pytest.raises(RuntimeError):
            database.update_skill_prompt("x", "s", "t")
        with pytest.raises(RuntimeError):
            database.snapshot_skill_version("x", "u", "r")
        with pytest.raises(RuntimeError):
            database.get_skill_versions("x")


# ── Strategy CRUD ─────────────────────────────────────────────────────


class TestStrategyCRUD:
    def test_add_strategy(self, db):
        """add_strategy inserts a strategy and returns its id."""
        sid = db.add_strategy("music_remix", "Music remix strategy")
        assert isinstance(sid, int)
        assert sid > 0

    def test_get_strategy(self, db):
        """get_strategy returns the correct strategy dict."""
        db.add_strategy("tech_review", "Tech review vids",
                        example_queries="best laptop 2025",
                        source="llm")
        s = db.get_strategy("tech_review")
        assert s is not None
        assert s["name"] == "tech_review"
        assert s["description"] == "Tech review vids"
        assert s["example_queries"] == "best laptop 2025"
        assert s["source"] == "llm"
        assert s["is_active"] == 1

    def test_get_strategy_not_found(self, db):
        """get_strategy returns None for a nonexistent name."""
        assert db.get_strategy("nope") is None

    def test_list_strategies_active_only(self, db):
        """list_strategies with active_only=True excludes retired strategies."""
        db.add_strategy("active_one", "desc")
        db.add_strategy("retired_one", "desc")
        db.retire_strategy("retired_one")

        active = db.list_strategies(active_only=True)
        names = [s["name"] for s in active]
        assert "active_one" in names
        assert "retired_one" not in names

    def test_list_strategies_all(self, db):
        """list_strategies with active_only=False includes retired strategies."""
        db.add_strategy("active_one", "desc")
        db.add_strategy("retired_one", "desc")
        db.retire_strategy("retired_one")

        all_strats = db.list_strategies(active_only=False)
        names = [s["name"] for s in all_strats]
        assert "active_one" in names
        assert "retired_one" in names

    def test_update_strategy_stats(self, db):
        """update_strategy_stats updates fields and derived rates."""
        sid = db.add_strategy("s1", "desc")
        db.update_strategy_stats(
            sid,
            total_queries=20,
            yielded_queries=8,
            total_transported=10,
            successful_transports=7,
            avg_bilibili_views=45000.0,
        )
        s = db.get_strategy("s1")
        assert s["total_queries"] == 20
        assert s["yielded_queries"] == 8
        assert abs(s["yield_rate"] - 0.4) < 1e-6
        assert abs(s["transport_success_rate"] - 0.7) < 1e-6
        assert s["avg_bilibili_views"] == 45000.0

    def test_update_strategy_stats_noop(self, db):
        """update_strategy_stats with no kwargs does nothing."""
        sid = db.add_strategy("s1", "desc")
        # Should not raise
        db.update_strategy_stats(sid)

    def test_retire_strategy(self, db):
        """retire_strategy sets is_active=0 and sets retired_at."""
        db.add_strategy("to_retire", "desc")
        db.retire_strategy("to_retire")
        s = db.get_strategy("to_retire")
        assert s["is_active"] == 0
        assert s["retired_at"] is not None

    def test_strategy_crud_not_connected(self):
        """Strategy CRUD methods raise RuntimeError when not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError):
            database.add_strategy("x", "d")
        with pytest.raises(RuntimeError):
            database.get_strategy("x")
        with pytest.raises(RuntimeError):
            database.list_strategies()
        with pytest.raises(RuntimeError):
            database.retire_strategy("x")
        with pytest.raises(RuntimeError):
            database.update_strategy_stats(1, total_queries=5)


# ── Strategy Run CRUD ─────────────────────────────────────────────────


class TestStrategyRunCRUD:
    def test_save_strategy_run(self, db):
        """save_strategy_run inserts a run and returns its id."""
        sid = db.add_strategy("s1", "desc")
        run_id = db.save_strategy_run(sid, "best laptop review 2025")
        assert isinstance(run_id, int)
        assert run_id > 0

    def test_update_strategy_run(self, db):
        """update_strategy_run sets allowed fields."""
        sid = db.add_strategy("s1", "desc")
        run_id = db.save_strategy_run(sid, "query1")
        db.update_strategy_run(
            run_id,
            query_result_count=15,
            yield_success=1,
            youtube_video_id="abc123",
            youtube_title="Test Video",
            youtube_views=100000,
        )
        row = db._conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert dict(row)["query_result_count"] == 15
        assert dict(row)["yield_success"] == 1
        assert dict(row)["youtube_video_id"] == "abc123"

    def test_update_strategy_run_ignores_unknown_keys(self, db):
        """update_strategy_run ignores keys not in the allowed set."""
        sid = db.add_strategy("s1", "desc")
        run_id = db.save_strategy_run(sid, "query1")
        # Should not raise, and unknown key is silently ignored
        db.update_strategy_run(run_id, unknown_field="value")
        row = db._conn.execute(
            "SELECT * FROM strategy_runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row is not None

    def test_update_strategy_run_noop(self, db):
        """update_strategy_run with no kwargs does nothing."""
        sid = db.add_strategy("s1", "desc")
        run_id = db.save_strategy_run(sid, "query1")
        db.update_strategy_run(run_id)  # no kwargs

    def test_get_strategy_yield_stats(self, db):
        """get_strategy_yield_stats returns per-strategy yield info."""
        sid = db.add_strategy("s1", "desc")
        db.update_strategy_stats(sid, total_queries=10, yielded_queries=3)

        stats = db.get_strategy_yield_stats()
        assert len(stats) == 1
        assert stats[0]["name"] == "s1"
        assert stats[0]["total_queries"] == 10
        assert stats[0]["yielded_queries"] == 3

    def test_get_latest_run_yields(self, db):
        """get_latest_run_yields returns recent runs with strategy name."""
        sid = db.add_strategy("s1", "desc")
        db.save_strategy_run(sid, "query_a")
        db.save_strategy_run(sid, "query_b")

        runs = db.get_latest_run_yields(limit=10)
        assert len(runs) == 2
        assert runs[0]["strategy_name"] == "s1"

    def test_get_latest_run_yields_respects_limit(self, db):
        """get_latest_run_yields respects the limit parameter."""
        sid = db.add_strategy("s1", "desc")
        for i in range(5):
            db.save_strategy_run(sid, f"query_{i}")

        runs = db.get_latest_run_yields(limit=2)
        assert len(runs) == 2

    def test_strategy_run_not_connected(self):
        """Strategy run methods raise RuntimeError when not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError):
            database.save_strategy_run(1, "q")
        with pytest.raises(RuntimeError):
            database.update_strategy_run(1, yield_success=1)
        with pytest.raises(RuntimeError):
            database.get_strategy_yield_stats()
        with pytest.raises(RuntimeError):
            database.get_latest_run_yields()


# ── Followed Channels ─────────────────────────────────────────────────


class TestFollowedChannels:
    def test_add_followed_channel(self, db):
        """add_followed_channel inserts a channel and returns its id."""
        cid = db.add_followed_channel(
            "TechLinked", youtube_channel_id="UC_abc",
            reason="Good tech content", source="llm", strategy_id=None,
        )
        assert isinstance(cid, int)

    def test_list_followed_channels(self, db):
        """list_followed_channels returns all active followed channels."""
        db.add_followed_channel("Channel A")
        db.add_followed_channel("Channel B")

        channels = db.list_followed_channels()
        names = [c["channel_name"] for c in channels]
        assert "Channel A" in names
        assert "Channel B" in names

    def test_list_followed_channels_empty(self, db):
        """list_followed_channels returns [] when none exist."""
        assert db.list_followed_channels() == []

    def test_add_duplicate_channel_ignored(self, db):
        """Adding a channel with the same name is ignored (INSERT OR IGNORE)."""
        db.add_followed_channel("Same Name")
        db.add_followed_channel("Same Name")
        channels = db.list_followed_channels()
        assert len(channels) == 1

    def test_followed_channels_not_connected(self):
        """Followed channel methods raise RuntimeError when not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError):
            database.add_followed_channel("x")
        with pytest.raises(RuntimeError):
            database.list_followed_channels()


# ── Scoring Params ────────────────────────────────────────────────────


class TestScoringParamsCRUD:
    def test_save_and_get_scoring_params(self, db):
        """save + get roundtrip for scoring params."""
        params_json = json.dumps({"engagement_weight": 0.35})
        db.save_scoring_params(params_json, source="test")

        result = db.get_scoring_params()
        assert result is not None
        assert result["params_json"] == params_json
        assert result["source"] == "test"

    def test_get_scoring_params_latest(self, db):
        """get_scoring_params returns the most recently saved params."""
        db.save_scoring_params('{"v":1}', "competitor")
        db.save_scoring_params('{"v":2}', "competitor")

        result = db.get_scoring_params()
        assert json.loads(result["params_json"])["v"] == 2

    def test_get_scoring_params_empty(self, db):
        """get_scoring_params returns None when no params exist."""
        assert db.get_scoring_params() is None

    def test_scoring_params_not_connected(self):
        """Scoring param methods raise RuntimeError when not connected."""
        database = Database(":memory:")
        with pytest.raises(RuntimeError):
            database.save_scoring_params("{}", "test")
        with pytest.raises(RuntimeError):
            database.get_scoring_params()
