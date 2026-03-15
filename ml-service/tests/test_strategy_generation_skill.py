import os
import sys
import json
from unittest.mock import MagicMock, patch, call
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.skills.strategy_generation import (
    StrategyGenerationSkill,
    DEFAULT_YOUTUBE_PRINCIPLES,
    DEFAULT_BILIBILI_PRINCIPLES,
)


@pytest.fixture
def mock_db():
    """Create a MagicMock database that returns None for get_skill (seeds defaults)."""
    db = MagicMock()
    db.get_skill.return_value = None
    db.upsert_skill.return_value = 1
    db.snapshot_skill_version.return_value = None
    db.update_skill_prompt.return_value = None
    return db


@pytest.fixture
def mock_backend():
    """Create a MagicMock LLM backend."""
    return MagicMock()


@pytest.fixture
def skill(mock_db, mock_backend):
    """Create a StrategyGenerationSkill with mocked db and backend."""
    return StrategyGenerationSkill(name="strategy_generation", db=mock_db, backend=mock_backend)


class TestSkillCreation:
    """Tests for StrategyGenerationSkill initialization."""

    def test_creates_with_default_name(self, mock_db, mock_backend):
        """Skill should default to name 'strategy_generation'."""
        s = StrategyGenerationSkill(db=mock_db, backend=mock_backend)
        assert s.name == "strategy_generation"

    def test_seeds_defaults_when_no_db_row(self, mock_db, mock_backend):
        """When db.get_skill returns None, upsert_skill should be called to seed defaults."""
        mock_db.get_skill.return_value = None
        s = StrategyGenerationSkill(db=mock_db, backend=mock_backend)
        mock_db.upsert_skill.assert_called_once()
        args = mock_db.upsert_skill.call_args
        assert args[0][0] == "strategy_generation"  # name
        assert "YouTube" in args[0][1]  # system_prompt mentions YouTube

    def test_loads_from_db_when_row_exists(self, mock_db, mock_backend):
        """When db.get_skill returns a row, skill should load from it."""
        mock_db.get_skill.return_value = {
            "system_prompt": "custom system prompt",
            "prompt_template": "custom template",
            "output_schema": '{"type": "object"}',
            "version": 5,
        }
        s = StrategyGenerationSkill(db=mock_db, backend=mock_backend)
        assert s.system_prompt == "custom system prompt"
        assert s.prompt_template == "custom template"
        assert s.version == 5
        mock_db.upsert_skill.assert_not_called()

    def test_default_principles_loaded(self, skill):
        """Skill should have default YouTube and Bilibili principles."""
        assert skill.youtube_principles == DEFAULT_YOUTUBE_PRINCIPLES
        assert skill.bilibili_principles == DEFAULT_BILIBILI_PRINCIPLES

    def test_version_starts_at_1(self, skill):
        """New skill should start at version 1."""
        assert skill.version == 1


class TestExecute:
    """Tests for the execute() method."""

    def test_execute_with_valid_context(self, skill, mock_backend):
        """Execute should call backend.chat and return parsed JSON."""
        mock_backend.chat.return_value = json.dumps({
            "queries": [
                {"query": "foreigner tries Chinese food", "strategy_name": "foreign_appreciation"}
            ],
            "new_strategy_proposals": [],
            "retire_suggestions": [],
        })
        context = {
            "strategies_with_full_context": "Strategy: food (yield: 50%)",
            "recent_outcomes_with_youtube_context": "(no recent outcomes)",
            "hot_words": "- Chinese food",
        }
        result = skill.execute(context)
        assert "queries" in result
        assert len(result["queries"]) == 1
        assert result["queries"][0]["query"] == "foreigner tries Chinese food"
        mock_backend.chat.assert_called_once()

    def test_execute_passes_json_schema(self, skill, mock_backend):
        """Execute should pass the output schema to backend.chat."""
        mock_backend.chat.return_value = '{"queries": []}'
        context = {
            "strategies_with_full_context": "",
            "recent_outcomes_with_youtube_context": "",
            "hot_words": "",
        }
        skill.execute(context)
        call_kwargs = mock_backend.chat.call_args
        assert call_kwargs[1]["json_schema"] is not None
        assert call_kwargs[1]["json_schema"]["required"] == ["queries"]

    def test_execute_with_empty_context_values(self, skill, mock_backend):
        """Execute should work with empty string context values."""
        mock_backend.chat.return_value = '{"queries": []}'
        context = {
            "strategies_with_full_context": "",
            "recent_outcomes_with_youtube_context": "",
            "hot_words": "",
        }
        result = skill.execute(context)
        assert result["queries"] == []

    def test_execute_renders_system_prompt_with_principles(self, skill, mock_backend):
        """Execute should render system prompt with current principles."""
        mock_backend.chat.return_value = '{"queries": []}'
        context = {
            "strategies_with_full_context": "",
            "recent_outcomes_with_youtube_context": "",
            "hot_words": "",
        }
        skill.execute(context)
        messages = mock_backend.chat.call_args[1]["messages"]
        system_msg = messages[0]["content"]
        assert "Specific queries outperform generic ones" in system_msg
        assert "Food and culture content performs well" in system_msg


class TestReflectOnYield:
    """Tests for the reflect_on_yield() method."""

    def test_reflect_on_yield_with_empty_data(self, skill):
        """reflect_on_yield with empty list should return None."""
        result = skill.reflect_on_yield([], [])
        assert result is None

    def test_reflect_on_yield_updates_principles(self, skill, mock_backend, mock_db):
        """reflect_on_yield should update youtube_principles when LLM returns them."""
        mock_backend.chat.return_value = json.dumps({
            "updated_youtube_principles": "- New principle: use channel handles",
            "new_strategies": [],
            "channels_to_follow": [],
            "retire": [],
            "analysis": "Found new patterns",
        })
        yield_data = [
            {"strategy_name": "food", "query": "chinese food review", "query_result_count": 5, "yield_success": 1, "youtube_title": "Best food"}
        ]
        strategy_stats = [
            {"name": "food", "total_queries": 10, "yielded_queries": 6, "yield_rate": 0.6}
        ]
        result = skill.reflect_on_yield(yield_data, strategy_stats)
        assert result is not None
        assert skill.youtube_principles == "- New principle: use channel handles"
        mock_db.snapshot_skill_version.assert_called_once()
        mock_db.update_skill_prompt.assert_called_once()

    def test_reflect_on_yield_no_update_when_no_principles(self, skill, mock_backend, mock_db):
        """reflect_on_yield should not update if LLM returns no updated_youtube_principles."""
        mock_backend.chat.return_value = json.dumps({
            "analysis": "No changes needed",
        })
        yield_data = [
            {"strategy_name": "food", "query": "test", "query_result_count": 0, "yield_success": 0}
        ]
        result = skill.reflect_on_yield(yield_data, [])
        assert result is not None
        assert skill.youtube_principles == DEFAULT_YOUTUBE_PRINCIPLES
        mock_db.update_skill_prompt.assert_not_called()


class TestReflectOnOutcomes:
    """Tests for the reflect_on_outcomes() method."""

    def test_reflect_on_outcomes_with_empty_data(self, skill):
        """reflect_on_outcomes with empty list should return None."""
        result = skill.reflect_on_outcomes([])
        assert result is None

    def test_reflect_on_outcomes_updates_bilibili_principles(self, skill, mock_backend, mock_db):
        """reflect_on_outcomes should update bilibili_principles when LLM returns them."""
        mock_backend.chat.return_value = json.dumps({
            "updated_bilibili_principles": "- Updated: focus on tech content",
            "scoring_insights": "Duration matters less than expected",
            "analysis": "Tech content outperforms",
        })
        outcomes = [
            {
                "outcome": "success",
                "youtube_title": "Tech Review",
                "youtube_views": 100000,
                "bilibili_views": 80000,
                "strategy_name": "tech",
                "query": "tech review",
            }
        ]
        result = skill.reflect_on_outcomes(outcomes)
        assert result is not None
        assert skill.bilibili_principles == "- Updated: focus on tech content"
        mock_db.snapshot_skill_version.assert_called_once()

    def test_reflect_on_outcomes_no_update_when_no_principles(self, skill, mock_backend, mock_db):
        """reflect_on_outcomes should not update if no updated_bilibili_principles returned."""
        mock_backend.chat.return_value = json.dumps({
            "analysis": "Insufficient data",
        })
        outcomes = [{"outcome": "failure", "youtube_title": "Bad", "youtube_views": 100,
                      "bilibili_views": 50, "strategy_name": "x", "query": "y"}]
        result = skill.reflect_on_outcomes(outcomes)
        assert skill.bilibili_principles == DEFAULT_BILIBILI_PRINCIPLES
        mock_db.update_skill_prompt.assert_not_called()


class TestFormatHelpers:
    """Tests for static formatting helper methods."""

    def test_format_strategies_context_with_data(self):
        """format_strategies_context should format strategies into readable text."""
        strategies = [
            {"name": "food", "description": "Food content", "yield_rate": 0.6,
             "example_queries": '["chinese food"]', "bilibili_check": "food check"},
        ]
        result = StrategyGenerationSkill.format_strategies_context(strategies)
        assert "food" in result
        assert "60%" in result
        assert "Food content" in result

    def test_format_strategies_context_empty(self):
        """format_strategies_context with empty list returns placeholder."""
        result = StrategyGenerationSkill.format_strategies_context([])
        assert result == "(no active strategies)"

    def test_format_recent_outcomes_with_data(self):
        """format_recent_outcomes should format outcomes with status prefixes."""
        outcomes = [
            {"outcome": "success", "query": "food review", "youtube_title": "Great Food",
             "youtube_views": 100000, "bilibili_views": 80000},
            {"outcome": "failure", "query": "bad query", "youtube_title": "Meh",
             "youtube_views": 5000, "bilibili_views": 100},
            {"outcome": "pending", "query": "new query", "youtube_title": "Pending",
             "youtube_views": 50000},
        ]
        result = StrategyGenerationSkill.format_recent_outcomes(outcomes)
        assert "[success]" in result
        assert "[failure]" in result
        assert "[pending]" in result
        assert "80,000 Bilibili views" in result

    def test_format_recent_outcomes_empty(self):
        """format_recent_outcomes with empty list returns placeholder."""
        result = StrategyGenerationSkill.format_recent_outcomes([])
        assert result == "(no recent outcomes)"

    def test_format_hot_words_with_data(self):
        """format_hot_words should list keywords with bullets."""
        hot_words = ["AI", "robotics", "EV"]
        result = StrategyGenerationSkill.format_hot_words(hot_words)
        assert "- AI" in result
        assert "- robotics" in result
        assert "- EV" in result

    def test_format_hot_words_empty(self):
        """format_hot_words with empty list returns placeholder."""
        result = StrategyGenerationSkill.format_hot_words([])
        assert result == "(no hot words available)"

    def test_format_hot_words_truncates_at_20(self):
        """format_hot_words should only show first 20 keywords."""
        hot_words = [f"kw_{i}" for i in range(30)]
        result = StrategyGenerationSkill.format_hot_words(hot_words)
        assert "kw_19" in result
        assert "kw_20" not in result

    def test_format_yield_report_with_data(self):
        """format_yield_report should categorize results as YIELD/LOW-Q/EMPTY."""
        yield_data = [
            {"strategy_name": "food", "query": "food review", "query_result_count": 10,
             "yield_success": 1, "youtube_title": "Great Food"},
            {"strategy_name": "tech", "query": "tech review", "query_result_count": 5,
             "yield_success": 0},
            {"strategy_name": "art", "query": "art showcase", "query_result_count": 0,
             "yield_success": 0},
        ]
        result = StrategyGenerationSkill.format_yield_report(yield_data)
        assert "[YIELD]" in result
        assert "[LOW-Q]" in result
        assert "[EMPTY]" in result

    def test_format_yield_report_empty(self):
        """format_yield_report with empty list returns placeholder."""
        result = StrategyGenerationSkill.format_yield_report([])
        assert result == "(no data)"

    def test_format_strategy_stats_with_data(self):
        """format_strategy_stats should format strategy stats with yield percentages."""
        stats = [
            {"name": "food", "total_queries": 20, "yielded_queries": 12, "yield_rate": 0.6},
            {"name": "tech", "total_queries": 10, "yielded_queries": 3, "yield_rate": 0.3},
        ]
        result = StrategyGenerationSkill.format_strategy_stats(stats)
        assert "food: yield 60%" in result
        assert "tech: yield 30%" in result

    def test_format_strategy_stats_empty(self):
        """format_strategy_stats with empty list returns placeholder."""
        result = StrategyGenerationSkill.format_strategy_stats([])
        assert result == "(no stats)"


class TestParseResponse:
    """Tests for LLM response parsing edge cases."""

    def test_parse_json_from_markdown_fences(self, skill, mock_backend):
        """Skill should extract JSON from markdown fenced blocks."""
        mock_backend.chat.return_value = '```json\n{"queries": [{"query": "test", "strategy_name": "food"}]}\n```'
        context = {
            "strategies_with_full_context": "",
            "recent_outcomes_with_youtube_context": "",
            "hot_words": "",
        }
        result = skill.execute(context)
        assert len(result["queries"]) == 1

    def test_parse_invalid_json_returns_raw(self, skill, mock_backend):
        """Invalid JSON should return dict with raw_response key."""
        mock_backend.chat.return_value = "This is not JSON at all"
        context = {
            "strategies_with_full_context": "",
            "recent_outcomes_with_youtube_context": "",
            "hot_words": "",
        }
        result = skill.execute(context)
        assert "raw_response" in result
