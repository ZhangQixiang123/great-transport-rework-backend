"""
Tests for Pydantic models in app.skills.models.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.skills.models import (
    GeneratedQuery,
    MarketAssessment,
    MarketReflectionOutput,
    NewStrategyProposal,
    OutcomeReflectionOutput,
    StrategyGenerationOutput,
    TransportabilityCheck,
    YieldReflectionOutput,
)


# ── GeneratedQuery ────────────────────────────────────────────────────


class TestGeneratedQuery:
    def test_create_with_defaults(self):
        """GeneratedQuery has sensible defaults for optional fields."""
        q = GeneratedQuery(query="best gaming mouse", strategy_name="tech_gadget")
        assert q.query == "best gaming mouse"
        assert q.strategy_name == "tech_gadget"
        assert q.bilibili_check == ""
        assert q.target_channels == []
        assert q.reasoning == ""

    def test_create_with_values(self):
        """GeneratedQuery accepts all fields."""
        q = GeneratedQuery(
            query="Python tutorial 2025",
            strategy_name="educational",
            bilibili_check="Python教程",
            target_channels=["TechWithTim", "Corey Schafer"],
            reasoning="Strong educational content with visual demos",
        )
        assert q.bilibili_check == "Python教程"
        assert len(q.target_channels) == 2

    def test_model_dump(self):
        """model_dump() returns a dict with all fields."""
        q = GeneratedQuery(query="q", strategy_name="s")
        d = q.model_dump()
        assert "query" in d
        assert "strategy_name" in d
        assert "bilibili_check" in d
        assert "target_channels" in d
        assert "reasoning" in d


# ── NewStrategyProposal ───────────────────────────────────────────────


class TestNewStrategyProposal:
    def test_create_with_defaults(self):
        """NewStrategyProposal has defaults for optional fields."""
        p = NewStrategyProposal(name="music_covers", description="Cover songs")
        assert p.youtube_tactics == ""
        assert p.example_queries == []
        assert p.target_channels == []
        assert p.bilibili_check == ""
        assert p.reasoning == ""

    def test_create_with_values(self):
        """NewStrategyProposal accepts all fields."""
        p = NewStrategyProposal(
            name="cooking",
            description="Cooking tutorial videos",
            youtube_tactics="Search for 'easy recipes'",
            example_queries=["5 minute meals", "budget cooking"],
            target_channels=["Tasty", "Binging with Babish"],
            bilibili_check="美食教程",
            reasoning="High engagement in food content",
        )
        assert len(p.example_queries) == 2
        assert p.target_channels[0] == "Tasty"

    def test_model_dump_structure(self):
        """model_dump() returns correct structure."""
        p = NewStrategyProposal(name="n", description="d")
        d = p.model_dump()
        assert isinstance(d["example_queries"], list)
        assert isinstance(d["target_channels"], list)


# ── StrategyGenerationOutput ──────────────────────────────────────────


class TestStrategyGenerationOutput:
    def test_create_with_defaults(self):
        """StrategyGenerationOutput defaults to empty lists."""
        out = StrategyGenerationOutput()
        assert out.queries == []
        assert out.new_strategy_proposals == []
        assert out.retire_suggestions == []

    def test_create_with_queries(self):
        """StrategyGenerationOutput accepts nested GeneratedQuery objects."""
        out = StrategyGenerationOutput(
            queries=[GeneratedQuery(query="test", strategy_name="s")],
            retire_suggestions=["old_strategy"],
        )
        assert len(out.queries) == 1
        assert out.queries[0].query == "test"
        assert out.retire_suggestions == ["old_strategy"]

    def test_model_dump_nested(self):
        """model_dump() correctly serializes nested models."""
        out = StrategyGenerationOutput(
            queries=[GeneratedQuery(query="q", strategy_name="s")],
            new_strategy_proposals=[
                NewStrategyProposal(name="n", description="d"),
            ],
        )
        d = out.model_dump()
        assert isinstance(d["queries"][0], dict)
        assert d["queries"][0]["query"] == "q"
        assert d["new_strategy_proposals"][0]["name"] == "n"


# ── MarketAssessment ──────────────────────────────────────────────────


class TestMarketAssessment:
    def test_create_with_defaults(self):
        """MarketAssessment has sensible defaults."""
        m = MarketAssessment()
        assert m.is_saturated is False
        assert m.opportunity_score == 0.5
        assert m.quality_gap == "medium"
        assert m.freshness_gap == "medium"
        assert m.reasoning == ""
        assert m.suggested_angle == ""

    def test_create_with_values(self):
        """MarketAssessment accepts all fields."""
        m = MarketAssessment(
            is_saturated=True,
            opportunity_score=0.2,
            quality_gap="small",
            freshness_gap="large",
            reasoning="Market is crowded",
            suggested_angle="Focus on niche sub-topic",
        )
        assert m.is_saturated is True
        assert m.opportunity_score == 0.2

    def test_opportunity_score_clamped_low(self):
        """opportunity_score below 0 raises validation error."""
        with pytest.raises(Exception):
            MarketAssessment(opportunity_score=-0.1)

    def test_opportunity_score_clamped_high(self):
        """opportunity_score above 1 raises validation error."""
        with pytest.raises(Exception):
            MarketAssessment(opportunity_score=1.5)

    def test_opportunity_score_boundary_zero(self):
        """opportunity_score of exactly 0.0 is valid."""
        m = MarketAssessment(opportunity_score=0.0)
        assert m.opportunity_score == 0.0

    def test_opportunity_score_boundary_one(self):
        """opportunity_score of exactly 1.0 is valid."""
        m = MarketAssessment(opportunity_score=1.0)
        assert m.opportunity_score == 1.0

    def test_model_dump(self):
        """model_dump() returns correct structure."""
        m = MarketAssessment(is_saturated=True, opportunity_score=0.8)
        d = m.model_dump()
        assert d["is_saturated"] is True
        assert d["opportunity_score"] == 0.8


# ── YieldReflectionOutput ────────────────────────────────────────────


class TestYieldReflectionOutput:
    def test_create_with_defaults(self):
        """YieldReflectionOutput defaults to empty values."""
        y = YieldReflectionOutput()
        assert y.updated_youtube_principles == ""
        assert y.new_strategies == []
        assert y.channels_to_follow == []
        assert y.retire == []
        assert y.analysis == ""

    def test_create_with_values(self):
        """YieldReflectionOutput accepts nested proposals."""
        y = YieldReflectionOutput(
            updated_youtube_principles="Focus on high-engagement content",
            new_strategies=[
                NewStrategyProposal(name="new_strat", description="test"),
            ],
            channels_to_follow=[{"name": "TechChannel", "reason": "good content"}],
            retire=["old_strat"],
            analysis="Analysis text here",
        )
        assert len(y.new_strategies) == 1
        assert y.retire == ["old_strat"]


# ── OutcomeReflectionOutput ──────────────────────────────────────────


class TestOutcomeReflectionOutput:
    def test_create_with_defaults(self):
        """OutcomeReflectionOutput defaults to empty strings."""
        o = OutcomeReflectionOutput()
        assert o.updated_bilibili_principles == ""
        assert o.scoring_insights == ""
        assert o.analysis == ""

    def test_create_with_values(self):
        """OutcomeReflectionOutput accepts all fields."""
        o = OutcomeReflectionOutput(
            updated_bilibili_principles="Prefer visual content",
            scoring_insights="Duration 5-15 min works best",
            analysis="Based on 50 outcomes",
        )
        assert o.updated_bilibili_principles == "Prefer visual content"


# ── MarketReflectionOutput ───────────────────────────────────────────


class TestMarketReflectionOutput:
    def test_create_with_defaults(self):
        """MarketReflectionOutput defaults to empty strings."""
        m = MarketReflectionOutput()
        assert m.updated_criteria == ""
        assert m.threshold_adjustments == ""
        assert m.analysis == ""

    def test_model_dump(self):
        """model_dump returns correct structure."""
        m = MarketReflectionOutput(
            updated_criteria="new criteria",
            analysis="reflection summary",
        )
        d = m.model_dump()
        assert d["updated_criteria"] == "new criteria"
        assert d["threshold_adjustments"] == ""


# ── TransportabilityCheck ────────────────────────────────────────────


class TestTransportabilityCheck:
    def test_create_with_defaults(self):
        """TransportabilityCheck defaults to transportable=True."""
        t = TransportabilityCheck()
        assert t.transportable is True
        assert t.reasoning == ""

    def test_create_not_transportable(self):
        """TransportabilityCheck can be set to not transportable."""
        t = TransportabilityCheck(
            transportable=False,
            reasoning="Content is heavily English-dependent",
        )
        assert t.transportable is False
        assert "English" in t.reasoning

    def test_model_dump(self):
        """model_dump() returns correct structure."""
        t = TransportabilityCheck(transportable=True, reasoning="Good fit")
        d = t.model_dump()
        assert d["transportable"] is True
        assert d["reasoning"] == "Good fit"
