import os
import sys
import json
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import Database
from app.cli import (
    cmd_bootstrap,
    cmd_strategy_list,
    cmd_strategy_add,
    cmd_follow_channel,
    cmd_skill_show,
    cmd_skill_history,
    cmd_skill_rollback,
)


@pytest.fixture
def db():
    """Create a real in-memory Database with skill tables initialized."""
    database = Database(":memory:")
    database.connect()
    database.ensure_skill_tables()
    yield database
    database.close()


def _make_args(**overrides):
    """Helper to create a MagicMock args object with attributes."""
    args = MagicMock()
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


class TestCmdBootstrap:
    """Tests for cmd_bootstrap()."""

    def test_bootstrap_skip_llm(self, db):
        """cmd_bootstrap with skip_llm=True should seed strategies."""
        args = _make_args(skip_llm=True, backend="ollama")
        result = cmd_bootstrap(db, args)
        assert result["command"] == "bootstrap"
        assert result["strategies_seeded"] == 8
        assert result["llm_principles"] is False

    def test_bootstrap_returns_command_key(self, db):
        """cmd_bootstrap result should always have 'command' key."""
        args = _make_args(skip_llm=True, backend="ollama")
        result = cmd_bootstrap(db, args)
        assert "command" in result
        assert result["command"] == "bootstrap"

    def test_bootstrap_idempotent(self, db):
        """Running cmd_bootstrap twice should not re-seed strategies."""
        args = _make_args(skip_llm=True, backend="ollama")
        result1 = cmd_bootstrap(db, args)
        result2 = cmd_bootstrap(db, args)
        assert result1["strategies_seeded"] == 8
        assert result2["strategies_seeded"] == 0


class TestCmdStrategyList:
    """Tests for cmd_strategy_list()."""

    def test_list_empty(self, db):
        """cmd_strategy_list with no strategies should return count=0."""
        args = _make_args()
        result = cmd_strategy_list(db, args)
        assert result["command"] == "strategy-list"
        assert result["count"] == 0
        assert result["strategies"] == []

    def test_list_after_bootstrap(self, db):
        """cmd_strategy_list after bootstrap should return all 8 strategies."""
        bootstrap_args = _make_args(skip_llm=True, backend="ollama")
        cmd_bootstrap(db, bootstrap_args)

        args = _make_args()
        result = cmd_strategy_list(db, args)
        assert result["command"] == "strategy-list"
        assert result["count"] == 8
        assert len(result["strategies"]) == 8

    def test_list_returns_strategy_dicts(self, db):
        """Strategies should be dicts with name and description."""
        bootstrap_args = _make_args(skip_llm=True, backend="ollama")
        cmd_bootstrap(db, bootstrap_args)

        args = _make_args()
        result = cmd_strategy_list(db, args)
        for s in result["strategies"]:
            assert "name" in s
            assert "description" in s


class TestCmdStrategyAdd:
    """Tests for cmd_strategy_add()."""

    def test_add_strategy(self, db):
        """cmd_strategy_add should add a new strategy."""
        args = _make_args(name="test_strategy", description="A test strategy", bilibili_check="test")
        result = cmd_strategy_add(db, args)
        assert result["command"] == "strategy-add"
        assert result["success"] is True
        assert result["name"] == "test_strategy"
        assert "strategy_id" in result

    def test_add_strategy_appears_in_list(self, db):
        """Added strategy should appear in strategy list."""
        add_args = _make_args(name="new_strat", description="New strategy", bilibili_check="")
        cmd_strategy_add(db, add_args)

        list_args = _make_args()
        result = cmd_strategy_list(db, list_args)
        names = [s["name"] for s in result["strategies"]]
        assert "new_strat" in names

    def test_add_strategy_with_empty_bilibili_check(self, db):
        """Adding strategy with empty bilibili_check should still succeed."""
        args = _make_args(name="no_check", description="No check", bilibili_check="")
        result = cmd_strategy_add(db, args)
        assert result["success"] is True

    def test_add_strategy_returns_id(self, db):
        """cmd_strategy_add should return the strategy_id."""
        args = _make_args(name="id_test", description="Test", bilibili_check="")
        result = cmd_strategy_add(db, args)
        assert isinstance(result["strategy_id"], int)
        assert result["strategy_id"] > 0


class TestCmdFollowChannel:
    """Tests for cmd_follow_channel()."""

    def test_follow_channel(self, db):
        """cmd_follow_channel should add a followed channel."""
        args = _make_args(channel_name="@testchannel", reason="Good content")
        result = cmd_follow_channel(db, args)
        assert result["command"] == "follow-channel"
        assert result["success"] is True
        assert result["channel_name"] == "@testchannel"

    def test_follow_channel_appears_in_list(self, db):
        """Followed channel should appear in followed channels list."""
        args = _make_args(channel_name="@mychannel", reason="")
        cmd_follow_channel(db, args)

        channels = db.list_followed_channels(active_only=True)
        names = [c["channel_name"] for c in channels]
        assert "@mychannel" in names

    def test_follow_channel_with_empty_reason(self, db):
        """Following a channel with empty reason should succeed."""
        args = _make_args(channel_name="@noreason", reason="")
        result = cmd_follow_channel(db, args)
        assert result["success"] is True


class TestCmdSkillShow:
    """Tests for cmd_skill_show()."""

    def test_skill_show_nonexistent(self, db):
        """cmd_skill_show with nonexistent skill should return error."""
        args = _make_args(skill_name="nonexistent_skill")
        result = cmd_skill_show(db, args)
        assert result["command"] == "skill-show"
        assert "error" in result
        assert "not found" in result["error"]

    def test_skill_show_existing(self, db):
        """cmd_skill_show with an existing skill should return skill details."""
        # First create a skill by upserting
        db.upsert_skill(
            "test_skill",
            "Test system prompt",
            "Test template {input}",
            '{"type": "object"}',
        )

        args = _make_args(skill_name="test_skill")
        result = cmd_skill_show(db, args)
        assert result["command"] == "skill-show"
        assert result["name"] == "test_skill"
        assert result["version"] == 1
        assert result["system_prompt"] == "Test system prompt"
        assert result["prompt_template"] == "Test template {input}"

    def test_skill_show_returns_updated_at(self, db):
        """cmd_skill_show should include updated_at timestamp."""
        db.upsert_skill("ts_skill", "sys", "tmpl", '{}')
        args = _make_args(skill_name="ts_skill")
        result = cmd_skill_show(db, args)
        assert "updated_at" in result

    def test_skill_show_after_bootstrap(self, db):
        """Skills created by bootstrap's Skill subclasses should be visible."""
        # Manually create a skill like bootstrap would
        from app.skills.strategy_generation import StrategyGenerationSkill
        mock_backend = MagicMock()
        skill = StrategyGenerationSkill(db=db, backend=mock_backend)

        args = _make_args(skill_name="strategy_generation")
        result = cmd_skill_show(db, args)
        assert result["command"] == "skill-show"
        assert result["name"] == "strategy_generation"
        assert "YouTube" in result["system_prompt"]


class TestCmdSkillHistory:
    """Tests for cmd_skill_history()."""

    def test_skill_history_empty(self, db):
        """cmd_skill_history with no versions should return empty list."""
        db.upsert_skill("empty_skill", "sys", "tmpl", '{}')
        args = _make_args(skill_name="empty_skill", limit=10)
        result = cmd_skill_history(db, args)
        assert result["command"] == "skill-history"
        assert result["skill_name"] == "empty_skill"
        assert result["versions"] == []

    def test_skill_history_after_update(self, db):
        """cmd_skill_history should show version after a prompt update."""
        db.upsert_skill("evolving_skill", "sys v1", "tmpl v1", '{}')
        db.snapshot_skill_version("evolving_skill", changed_by="test", reason="update")
        db.update_skill_prompt("evolving_skill", "sys v2", "tmpl v2")

        args = _make_args(skill_name="evolving_skill", limit=10)
        result = cmd_skill_history(db, args)
        assert len(result["versions"]) == 1
        assert result["versions"][0]["changed_by"] == "test"

    def test_skill_history_respects_limit(self, db):
        """cmd_skill_history should respect the limit parameter."""
        db.upsert_skill("limited_skill", "sys", "tmpl", '{}')
        for i in range(5):
            db.snapshot_skill_version("limited_skill", changed_by=f"test{i}", reason=f"update {i}")

        args = _make_args(skill_name="limited_skill", limit=3)
        result = cmd_skill_history(db, args)
        assert len(result["versions"]) == 3

    def test_skill_history_nonexistent_skill(self, db):
        """cmd_skill_history for a nonexistent skill should return empty versions."""
        args = _make_args(skill_name="does_not_exist", limit=10)
        result = cmd_skill_history(db, args)
        assert result["command"] == "skill-history"
        assert result["versions"] == []


class TestCmdSkillRollback:
    """Tests for cmd_skill_rollback()."""

    def test_rollback_invalid_version(self, db):
        """cmd_skill_rollback with nonexistent version should return success=False."""
        db.upsert_skill("rollback_skill", "sys", "tmpl", '{}')
        args = _make_args(skill_name="rollback_skill", version=999)
        result = cmd_skill_rollback(db, args)
        assert result["command"] == "skill-rollback"
        assert result["success"] is False
        assert result["target_version"] == 999

    def test_rollback_to_valid_version(self, db):
        """cmd_skill_rollback to an existing version should return success=True."""
        db.upsert_skill("rb_skill", "sys v1", "tmpl v1", '{}')
        # Snapshot version 1 before updating
        db.snapshot_skill_version("rb_skill", changed_by="test", reason="preparing v2")
        db.update_skill_prompt("rb_skill", "sys v2", "tmpl v2")

        args = _make_args(skill_name="rb_skill", version=1)
        result = cmd_skill_rollback(db, args)
        assert result["command"] == "skill-rollback"
        assert result["success"] is True
        assert result["skill_name"] == "rb_skill"

        # Verify the prompts were rolled back
        skill = db.get_skill("rb_skill")
        assert skill["system_prompt"] == "sys v1"
        assert skill["prompt_template"] == "tmpl v1"

    def test_rollback_returns_command_key(self, db):
        """cmd_skill_rollback should always include 'command' key."""
        db.upsert_skill("cmd_skill", "sys", "tmpl", '{}')
        args = _make_args(skill_name="cmd_skill", version=1)
        result = cmd_skill_rollback(db, args)
        assert "command" in result
        assert result["command"] == "skill-rollback"

    def test_rollback_nonexistent_skill_creates_default(self, db):
        """Rollback on a skill that does not exist yet should handle gracefully."""
        # The Skill constructor calls get_skill, which returns None, so it seeds defaults
        # Then rollback on a version that doesn't exist should return False
        args = _make_args(skill_name="fresh_skill", version=5)
        result = cmd_skill_rollback(db, args)
        assert result["success"] is False

    def test_rollback_preserves_snapshot(self, db):
        """Rollback should create a snapshot of current state before rolling back."""
        db.upsert_skill("snap_skill", "original sys", "original tmpl", '{}')
        db.snapshot_skill_version("snap_skill", changed_by="test", reason="v1 snapshot")
        db.update_skill_prompt("snap_skill", "modified sys", "modified tmpl")

        args = _make_args(skill_name="snap_skill", version=1)
        cmd_skill_rollback(db, args)

        # Should have 2 snapshots now: the original v1 and the rollback snapshot
        versions = db.get_skill_versions("snap_skill")
        assert len(versions) >= 2
        # One of them should be from "rollback"
        changed_by_values = [v["changed_by"] for v in versions]
        assert "rollback" in changed_by_values
