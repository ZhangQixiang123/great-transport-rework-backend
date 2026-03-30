"""Tests for heuristic scoring."""
import json
import pytest

from app.personas._shared.scoring import ScoringParams, heuristic_score


def test_scoring_params_default():
    p = ScoringParams()
    assert p.engagement_good_threshold == 0.04
    assert p.duration_sweet_spot == (300, 900)
    assert p.youtube_min_views == 10_000


def test_scoring_params_json_roundtrip():
    p = ScoringParams(
        category_bonuses={20: 1.5, 28: 1.2},
        bilibili_success_threshold=80000,
    )
    json_str = p.to_json()
    p2 = ScoringParams.from_json(json_str)
    assert p2.category_bonuses[20] == 1.5
    assert p2.bilibili_success_threshold == 80000


def test_heuristic_score_basic():
    params = ScoringParams()
    score = heuristic_score(
        candidate_views=100_000,
        likes=5000,
        duration=600,
        category_id=20,
        opportunity_score=0.7,
        params=params,
    )
    assert score > 0
    assert isinstance(score, float)


def test_heuristic_score_duration_penalty():
    params = ScoringParams()
    score_good = heuristic_score(500_000, 20000, 600, 20, 0.5, params)
    score_bad = heuristic_score(500_000, 20000, 3600, 20, 0.5, params)
    # Videos in sweet spot duration should score higher
    assert score_good > score_bad


def test_heuristic_score_category_bonus():
    params = ScoringParams(category_bonuses={20: 2.0})
    score_bonus = heuristic_score(100_000, 5000, 600, 20, 0.5, params)
    score_normal = heuristic_score(100_000, 5000, 600, 99, 0.5, params)
    assert score_bonus > score_normal
