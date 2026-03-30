"""Tests for transportability check — shared function."""
import json
import pytest

from app.personas._shared.transportability import check_transportability, _check_content_safety
from tests.conftest import MockLLMBackend


def test_hard_block_ccp():
    reason = _check_content_safety("Why the CCP is losing control")
    assert reason is not None
    assert "CCP" in reason


def test_hard_block_tiananmen():
    reason = _check_content_safety("Tiananmen Square 1989 truth")
    assert reason is not None


def test_safe_title_passes():
    reason = _check_content_safety("Best Gaming Headphones 2026")
    assert reason is None


def test_blocked_title_returns_false():
    backend = MockLLMBackend()
    result = check_transportability(
        backend=backend,
        title="Xi Jinping's secret plan",
        channel="Test",
        duration_seconds=600,
        category_id=25,
    )
    assert result["transportable"] is False
    assert "BLOCKED" in result["reasoning"]
    assert len(backend.calls) == 0  # LLM not called


def test_llm_approval():
    backend = MockLLMBackend(response=json.dumps({
        "transportable": True,
        "persona_fit": 0.8,
        "reasoning": "Great gaming content",
    }))
    result = check_transportability(
        backend=backend,
        title="Best Gaming Moments 2026",
        channel="GameChannel",
        duration_seconds=900,
        category_id=20,
        persona_fit_prompt="Sarcastic gaming AI",
        persona_fit_threshold=0.3,
    )
    assert result["transportable"] is True
    assert result["persona_fit"] == 0.8
    assert len(backend.calls) == 1


def test_low_persona_fit_rejects():
    backend = MockLLMBackend(response=json.dumps({
        "transportable": True,
        "persona_fit": 0.1,
        "reasoning": "Not a good fit",
    }))
    result = check_transportability(
        backend=backend,
        title="Meditation for beginners",
        channel="ZenChannel",
        duration_seconds=1200,
        category_id=22,
        persona_fit_threshold=0.3,
    )
    assert result["transportable"] is False
    assert "Low persona fit" in result["reasoning"]
