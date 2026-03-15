import os
import sys
import json
from unittest.mock import MagicMock, patch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.skills.market_analysis import (
    MarketAnalysisSkill,
    DEFAULT_CRITERIA,
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
    """Create a MarketAnalysisSkill with mocked db and backend."""
    return MarketAnalysisSkill(name="market_analysis", db=mock_db, backend=mock_backend)


def _make_context(**overrides):
    """Helper to create a valid context dict for market analysis."""
    defaults = {
        "bilibili_check": "外国人 中国",
        "total": 15,
        "high_view_count": 3,
        "recent_count": 5,
        "min_views": 200,
        "max_views": 500000,
        "top_videos_with_dates": "1. [500K views] Great Video (2026-01-15)",
    }
    defaults.update(overrides)
    return defaults


class TestSkillCreation:
    """Tests for MarketAnalysisSkill initialization."""

    def test_creates_with_default_name(self, mock_db, mock_backend):
        """Skill should default to name 'market_analysis'."""
        s = MarketAnalysisSkill(db=mock_db, backend=mock_backend)
        assert s.name == "market_analysis"

    def test_seeds_defaults_when_no_db_row(self, mock_db, mock_backend):
        """When db.get_skill returns None, upsert_skill should seed defaults."""
        mock_db.get_skill.return_value = None
        s = MarketAnalysisSkill(db=mock_db, backend=mock_backend)
        mock_db.upsert_skill.assert_called_once()
        args = mock_db.upsert_skill.call_args
        assert args[0][0] == "market_analysis"
        assert "Bilibili" in args[0][1]

    def test_loads_from_db_when_row_exists(self, mock_db, mock_backend):
        """When db.get_skill returns a row, skill should load from it."""
        mock_db.get_skill.return_value = {
            "system_prompt": "custom system",
            "prompt_template": "custom template",
            "output_schema": '{"type": "object"}',
            "version": 3,
        }
        s = MarketAnalysisSkill(db=mock_db, backend=mock_backend)
        assert s.system_prompt == "custom system"
        assert s.version == 3
        mock_db.upsert_skill.assert_not_called()

    def test_default_criteria_loaded(self, skill):
        """Skill should have default learned criteria."""
        assert skill.learned_criteria == DEFAULT_CRITERIA
        assert ">5 videos" in skill.learned_criteria


class TestExecute:
    """Tests for the execute() method."""

    def test_execute_with_bilibili_search_context(self, skill, mock_backend):
        """Execute should call backend.chat with context and return parsed result."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": False,
            "opportunity_score": 0.75,
            "quality_gap": "large",
            "freshness_gap": "medium",
            "reasoning": "Low competition in this niche",
            "suggested_angle": "Focus on authentic reactions",
        })
        context = _make_context()
        result = skill.execute(context)

        assert result["is_saturated"] is False
        assert result["opportunity_score"] == 0.75
        assert result["quality_gap"] == "large"
        mock_backend.chat.assert_called_once()

    def test_execute_passes_output_schema(self, skill, mock_backend):
        """Execute should pass the market analysis output schema to backend.chat."""
        mock_backend.chat.return_value = '{"is_saturated": true, "opportunity_score": 0.2}'
        context = _make_context()
        skill.execute(context)
        call_kwargs = mock_backend.chat.call_args
        schema = call_kwargs[1]["json_schema"]
        assert "is_saturated" in schema["properties"]
        assert "opportunity_score" in schema["properties"]
        assert schema["required"] == ["is_saturated", "opportunity_score"]

    def test_execute_renders_system_prompt_with_criteria(self, skill, mock_backend):
        """Execute should render system prompt with learned criteria."""
        mock_backend.chat.return_value = '{"is_saturated": false, "opportunity_score": 0.5}'
        context = _make_context()
        skill.execute(context)
        messages = mock_backend.chat.call_args[1]["messages"]
        system_msg = messages[0]["content"]
        assert ">5 videos" in system_msg
        assert "freshness gap" in system_msg


class TestOpportunityScoreClamping:
    """Tests for opportunity_score clamping to [0, 1]."""

    def test_clamps_score_above_1(self, skill, mock_backend):
        """Opportunity score above 1.0 should be clamped to 1.0."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": False,
            "opportunity_score": 1.5,
        })
        context = _make_context()
        result = skill.execute(context)
        assert result["opportunity_score"] == 1.0

    def test_clamps_score_below_0(self, skill, mock_backend):
        """Opportunity score below 0.0 should be clamped to 0.0."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": True,
            "opportunity_score": -0.3,
        })
        context = _make_context()
        result = skill.execute(context)
        assert result["opportunity_score"] == 0.0

    def test_score_within_range_unchanged(self, skill, mock_backend):
        """Opportunity score within [0, 1] should remain unchanged."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": False,
            "opportunity_score": 0.42,
        })
        context = _make_context()
        result = skill.execute(context)
        assert abs(result["opportunity_score"] - 0.42) < 1e-6

    def test_score_at_boundary_0(self, skill, mock_backend):
        """Opportunity score of exactly 0 should remain 0."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": True,
            "opportunity_score": 0.0,
        })
        context = _make_context()
        result = skill.execute(context)
        assert result["opportunity_score"] == 0.0

    def test_score_at_boundary_1(self, skill, mock_backend):
        """Opportunity score of exactly 1 should remain 1."""
        mock_backend.chat.return_value = json.dumps({
            "is_saturated": False,
            "opportunity_score": 1.0,
        })
        context = _make_context()
        result = skill.execute(context)
        assert result["opportunity_score"] == 1.0


class TestReflectOnOutcomes:
    """Tests for the reflect_on_outcomes() method."""

    def test_reflect_with_empty_outcomes(self, skill):
        """reflect_on_outcomes with empty list should return None."""
        result = skill.reflect_on_outcomes([])
        assert result is None

    def test_reflect_updates_learned_criteria(self, skill, mock_backend, mock_db):
        """reflect_on_outcomes should update learned_criteria when LLM provides them."""
        mock_backend.chat.return_value = json.dumps({
            "updated_criteria": "- New: look for quality gaps in top 10 results",
            "threshold_adjustments": "Lower threshold to 3 videos",
            "analysis": "Our thresholds were too strict",
        })
        outcomes = [
            {
                "bilibili_views": 120000,
                "bilibili_novelty_score": 0.8,
                "bilibili_check": "外国人 食物",
                "outcome": "success",
            },
            {
                "bilibili_views": 2000,
                "bilibili_novelty_score": 0.3,
                "bilibili_check": "科普",
                "outcome": "failure",
            },
        ]
        result = skill.reflect_on_outcomes(outcomes)
        assert result is not None
        assert skill.learned_criteria == "- New: look for quality gaps in top 10 results"
        mock_db.snapshot_skill_version.assert_called_once()
        mock_db.update_skill_prompt.assert_called_once()

    def test_reflect_no_update_when_no_criteria(self, skill, mock_backend, mock_db):
        """reflect_on_outcomes should not update if no updated_criteria returned."""
        mock_backend.chat.return_value = json.dumps({
            "analysis": "Need more data",
        })
        outcomes = [{"bilibili_views": 5000, "bilibili_novelty_score": 0.5,
                      "bilibili_check": "test", "outcome": "failure"}]
        result = skill.reflect_on_outcomes(outcomes)
        assert skill.learned_criteria == DEFAULT_CRITERIA
        mock_db.update_skill_prompt.assert_not_called()

    def test_reflect_snapshot_reason_includes_analysis(self, skill, mock_backend, mock_db):
        """The snapshot reason should include the analysis text from LLM response."""
        mock_backend.chat.return_value = json.dumps({
            "updated_criteria": "- Updated criteria",
            "analysis": "Thresholds were wrong",
        })
        outcomes = [{"bilibili_views": 5000, "bilibili_novelty_score": 0.5,
                      "bilibili_check": "test", "outcome": "failure"}]
        skill.reflect_on_outcomes(outcomes)
        snapshot_call = mock_db.snapshot_skill_version.call_args
        assert "Thresholds were wrong" in snapshot_call[1].get("reason", "") or \
               "Thresholds were wrong" in (snapshot_call[0][2] if len(snapshot_call[0]) > 2 else "")


class TestFormatJudgmentOutcomes:
    """Tests for the _format_judgment_outcomes() static method."""

    def test_format_with_data(self):
        """_format_judgment_outcomes should format outcomes with status and scores."""
        outcomes = [
            {
                "bilibili_views": 120000,
                "bilibili_novelty_score": 0.85,
                "bilibili_check": "外国人 中国",
                "outcome": "success",
            },
            {
                "bilibili_views": 500,
                "bilibili_novelty_score": 0.2,
                "bilibili_check": "科普 英文",
                "outcome": "failure",
            },
        ]
        result = MarketAnalysisSkill._format_judgment_outcomes(outcomes)
        assert "[SUCCESS]" in result
        assert "[FAILURE]" in result
        assert "120,000" in result
        assert "0.85" in result

    def test_format_empty_list(self):
        """_format_judgment_outcomes with empty list returns placeholder."""
        result = MarketAnalysisSkill._format_judgment_outcomes([])
        assert result == "(no outcomes)"

    def test_format_with_missing_keys(self):
        """_format_judgment_outcomes should handle missing keys gracefully."""
        outcomes = [{"outcome": "unknown"}]
        result = MarketAnalysisSkill._format_judgment_outcomes(outcomes)
        assert "[UNKNOWN]" in result
        assert "0" in result  # default views

    def test_format_novelty_precision(self):
        """Novelty score should be formatted to 2 decimal places."""
        outcomes = [
            {"bilibili_views": 1000, "bilibili_novelty_score": 0.12345,
             "bilibili_check": "test", "outcome": "failure"},
        ]
        result = MarketAnalysisSkill._format_judgment_outcomes(outcomes)
        assert "0.12" in result


class TestWithDefaultCriteriaLoaded:
    """Tests verifying skill behavior when default criteria are loaded."""

    def test_system_prompt_contains_criteria(self, skill):
        """System prompt should contain the learned criteria placeholder."""
        default_prompt = skill._default_system_prompt()
        assert "{learned_criteria}" in default_prompt

    def test_prompt_template_contains_required_fields(self, skill):
        """Prompt template should contain required context fields."""
        template = skill._default_prompt_template()
        assert "{bilibili_check}" in template
        assert "{total}" in template
        assert "{high_view_count}" in template
        assert "{recent_count}" in template

    def test_output_schema_has_required_fields(self, skill):
        """Output schema should require is_saturated and opportunity_score."""
        schema = skill._output_schema()
        assert "is_saturated" in schema["properties"]
        assert "opportunity_score" in schema["properties"]
        assert set(schema["required"]) == {"is_saturated", "opportunity_score"}
