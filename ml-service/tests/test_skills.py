"""Tests for Skill base class, StrategyGenerationSkill, MarketAnalysisSkill."""
import json
import pytest

from tests.conftest import MockLLMBackend


def test_skill_base_seeds_defaults(db):
    """Skill seeds its defaults into DB on first creation."""
    from app.skills.base import Skill
    backend = MockLLMBackend()
    skill = Skill("test_skill", db=db, backend=backend)
    assert skill.version == 1
    assert skill.system_prompt == "You are a helpful assistant. Respond in JSON."

    # Check it was persisted
    row = db.get_skill("test_skill")
    assert row is not None
    assert row["name"] == "test_skill"


def test_skill_loads_from_db(db):
    """Skill loads existing state from DB instead of seeding."""
    from app.skills.base import Skill
    backend = MockLLMBackend()

    # Create first
    s1 = Skill("reload_test", db=db, backend=backend)
    assert s1.version == 1

    # Modify in DB
    db.update_skill_prompt("reload_test", "custom prompt", "custom template")

    # Create second — should load from DB
    s2 = Skill("reload_test", db=db, backend=backend)
    assert s2.system_prompt == "custom prompt"
    assert s2.prompt_template == "custom template"
    assert s2.version == 2  # update_skill_prompt increments version


def test_skill_parse_response_json(db):
    from app.skills.base import Skill
    s = Skill("parse_test", db=db, backend=MockLLMBackend())
    result = s._parse_response('{"key": "value"}')
    assert result == {"key": "value"}


def test_skill_parse_response_markdown_fence(db):
    from app.skills.base import Skill
    s = Skill("parse_test2", db=db, backend=MockLLMBackend())
    result = s._parse_response('```json\n{"a": 1}\n```')
    assert result == {"a": 1}


def test_skill_parse_response_trailing_comma(db):
    from app.skills.base import Skill
    s = Skill("parse_test3", db=db, backend=MockLLMBackend())
    result = s._parse_response('{"a": 1, "b": 2,}')
    assert result == {"a": 1, "b": 2}


def test_strategy_generation_skill_init(db):
    from app.skills import StrategyGenerationSkill
    backend = MockLLMBackend()
    skill = StrategyGenerationSkill(db=db, backend=backend)
    assert skill.name == "strategy_generation"
    assert "{youtube_principles}" in skill._default_system_prompt()


def test_strategy_generation_execute(db):
    from app.skills import StrategyGenerationSkill
    response = json.dumps({
        "queries": [
            {"query": "test query", "strategy_name": "gaming_deep_dive"}
        ],
        "retire_suggestions": [],
    })
    backend = MockLLMBackend(response=response)
    skill = StrategyGenerationSkill(db=db, backend=backend)
    result = skill.execute({
        "strategies_with_full_context": "test",
        "recent_outcomes_with_youtube_context": "test",
        "hot_words": "test",
    })
    assert "queries" in result
    assert len(result["queries"]) == 1
    assert len(backend.calls) == 1


def test_market_analysis_skill_execute(db):
    from app.skills import MarketAnalysisSkill
    response = json.dumps({
        "is_saturated": False,
        "opportunity_score": 0.8,
        "reasoning": "Low competition",
    })
    backend = MockLLMBackend(response=response)
    skill = MarketAnalysisSkill(db=db, backend=backend)
    result = skill.execute({
        "bilibili_check": "test",
        "total": 5,
        "high_view_count": 1,
        "recent_count": 2,
        "min_views": 100,
        "max_views": 50000,
        "top_videos_with_dates": "video1",
    })
    assert result["is_saturated"] is False
    assert result["opportunity_score"] == 0.8


def test_strategy_generation_formatting():
    from app.skills import StrategyGenerationSkill
    # Test static formatting methods
    assert "(no active strategies)" == StrategyGenerationSkill.format_strategies_context([])
    assert "(no hot words available)" == StrategyGenerationSkill.format_hot_words([])
    assert "(no data)" == StrategyGenerationSkill.format_yield_report([])
