"""Tests for persona self-bootstrap (replaces global bootstrap)."""
import pytest

from app.personas.sarcastic_ai.strategies import (
    SARCASTIC_AI_STRATEGIES, bootstrap_strategies, bootstrap_scoring,
)


def test_sarcastic_ai_strategies_count():
    assert len(SARCASTIC_AI_STRATEGIES) == 6


def test_sarcastic_ai_strategies_structure():
    for s in SARCASTIC_AI_STRATEGIES:
        assert "name" in s
        assert "description" in s
        assert "bilibili_check" in s


def test_bootstrap_strategies(db):
    count = bootstrap_strategies(db, persona_id="sarcastic_ai")
    assert count == 6

    # Second call should seed 0 (idempotent)
    count2 = bootstrap_strategies(db, persona_id="sarcastic_ai")
    assert count2 == 0


def test_bootstrap_strategies_persona_isolation(db):
    """Same strategy names for different personas should not collide."""
    count1 = bootstrap_strategies(db, persona_id="persona_a")
    count2 = bootstrap_strategies(db, persona_id="persona_b")
    assert count1 == 6
    assert count2 == 6

    # Each persona has its own strategies
    a_strategies = db.list_strategies(persona_id="persona_a")
    b_strategies = db.list_strategies(persona_id="persona_b")
    assert len(a_strategies) == 6
    assert len(b_strategies) == 6


def test_bootstrap_scoring(db):
    bootstrap_scoring(db, persona_id="sarcastic_ai")
    params = db.get_scoring_params(persona_id="sarcastic_ai")
    assert params is not None


def test_bootstrap_scoring_idempotent(db):
    bootstrap_scoring(db, persona_id="sarcastic_ai")
    bootstrap_scoring(db, persona_id="sarcastic_ai")
    # Should still have exactly one entry (idempotent)
    params = db.get_scoring_params(persona_id="sarcastic_ai")
    assert params is not None
