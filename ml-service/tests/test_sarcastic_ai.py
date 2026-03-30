# -*- coding: utf-8 -*-
"""Tests for SarcasticAI persona."""
import pytest

from app.personas.sarcastic_ai import SarcasticAI
from app.personas.sarcastic_ai.config import PERSONA_ID, CONTENT_AFFINITY
from app.personas.sarcastic_ai.prompts import (
    SYSTEM_PROMPT, STRATEGY_HINTS, FEW_SHOT_EXAMPLES,
    HIGH_TSUNDERE, MID_TSUNDERE, LOW_TSUNDERE,
    get_intensity, get_temperature, sample_few_shot,
)
from app.personas.protocol import Persona, RunContext, RunResult


def test_sarcastic_ai_is_persona():
    p = SarcasticAI()
    assert isinstance(p, Persona)


def test_persona_id():
    p = SarcasticAI()
    assert p.persona_id == "sarcastic_ai"


def test_system_prompt_not_empty():
    assert len(SYSTEM_PROMPT) > 100


def test_strategy_hints_coverage():
    """All 9 strategies should have hints."""
    assert len(STRATEGY_HINTS) == 9
    for strategy in HIGH_TSUNDERE + MID_TSUNDERE + LOW_TSUNDERE:
        assert strategy in STRATEGY_HINTS


def test_few_shot_examples_count():
    assert len(FEW_SHOT_EXAMPLES) == 10


def test_few_shot_examples_structure():
    for ex in FEW_SHOT_EXAMPLES:
        assert "input" in ex
        assert "output" in ex
        assert "intensity" in ex
        assert ex["intensity"] in ("high", "mid", "low")


def test_get_intensity():
    assert get_intensity("gaming_deep_dive") == "high"
    assert get_intensity("educational_explainer") == "mid"
    assert get_intensity("geopolitics_hot_take") == "low"
    assert get_intensity("unknown_strategy") == "mid"  # default


def test_get_temperature():
    assert get_temperature("geopolitics_hot_take") == 0.7
    assert get_temperature("gaming_deep_dive") == 1.0
    assert get_temperature("educational_explainer") == 1.0


def test_sample_few_shot_returns_correct_count():
    examples = sample_few_shot("gaming_deep_dive", count=3)
    assert len(examples) == 3


def test_sample_few_shot_biased_high():
    # Run multiple times to verify bias
    from collections import Counter
    intensities = Counter()
    for _ in range(100):
        examples = sample_few_shot("gaming_deep_dive", count=3)
        for ex in examples:
            intensities[ex["intensity"]] += 1
    # High intensity should dominate
    assert intensities["high"] > intensities.get("low", 0)


def test_content_affinity():
    assert 20 in CONTENT_AFFINITY  # Gaming
    assert CONTENT_AFFINITY[20] >= 0.7


def test_run_context_defaults():
    ctx = RunContext()
    assert ctx.dry_run is False
    assert ctx.quota_budget == 2000
