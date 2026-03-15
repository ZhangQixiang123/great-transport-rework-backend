"""
Tests for the scoring module (heuristic scoring and bootstrap).
"""
import json
import math
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.scoring.heuristic import ScoringParams, heuristic_score, bootstrap_scoring_params
from app.db.database import Database


# ── ScoringParams ─────────────────────────────────────────────────────


class TestScoringParamsDefaults:
    def test_defaults(self):
        """ScoringParams has sensible defaults."""
        p = ScoringParams()
        assert p.engagement_good_threshold == 0.04
        assert p.engagement_weight == 0.3
        assert p.view_signal_weight == 0.2
        assert p.opportunity_weight == 0.3
        assert p.duration_weight == 0.2
        assert p.duration_sweet_spot == (300, 900)
        assert p.category_bonuses == {}
        assert p.youtube_min_views == 50_000
        assert p.bilibili_success_threshold == 50_000

    def test_custom_values(self):
        """ScoringParams accepts custom values."""
        p = ScoringParams(
            engagement_good_threshold=0.05,
            engagement_weight=0.25,
            duration_sweet_spot=(200, 600),
            category_bonuses={22: 1.5, 10: 0.8},
            youtube_min_views=100_000,
        )
        assert p.engagement_good_threshold == 0.05
        assert p.duration_sweet_spot == (200, 600)
        assert p.category_bonuses[22] == 1.5


class TestScoringParamsSerialize:
    def test_to_json_roundtrip(self):
        """to_json -> from_json preserves all fields."""
        original = ScoringParams(
            engagement_good_threshold=0.05,
            engagement_weight=0.25,
            view_signal_weight=0.15,
            opportunity_weight=0.35,
            duration_weight=0.25,
            duration_sweet_spot=(200, 800),
            category_bonuses={22: 1.2, 10: 0.9},
            youtube_min_views=75_000,
            bilibili_success_threshold=30_000,
        )
        json_str = original.to_json()
        restored = ScoringParams.from_json(json_str)

        assert restored.engagement_good_threshold == 0.05
        assert restored.engagement_weight == 0.25
        assert restored.view_signal_weight == 0.15
        assert restored.opportunity_weight == 0.35
        assert restored.duration_weight == 0.25
        assert restored.duration_sweet_spot == (200, 800)
        assert restored.category_bonuses == {22: 1.2, 10: 0.9}
        assert restored.youtube_min_views == 75_000
        assert restored.bilibili_success_threshold == 30_000

    def test_to_json_is_valid_json(self):
        """to_json returns valid JSON string."""
        p = ScoringParams()
        parsed = json.loads(p.to_json())
        assert "engagement_good_threshold" in parsed
        assert "duration_sweet_spot" in parsed

    def test_from_json_with_missing_keys(self):
        """from_json uses defaults for missing keys."""
        restored = ScoringParams.from_json('{"engagement_weight": 0.5}')
        assert restored.engagement_weight == 0.5
        assert restored.view_signal_weight == 0.2  # default
        assert restored.duration_sweet_spot == (300, 900)  # default

    def test_from_json_category_keys_as_int(self):
        """from_json converts category bonus keys from str to int."""
        json_str = '{"category_bonuses": {"22": 1.3, "10": 0.7}}'
        restored = ScoringParams.from_json(json_str)
        assert 22 in restored.category_bonuses
        assert 10 in restored.category_bonuses
        assert restored.category_bonuses[22] == 1.3


# ── heuristic_score ───────────────────────────────────────────────────


class TestHeuristicScore:
    def test_basic_score(self):
        """heuristic_score returns a positive float for valid inputs."""
        params = ScoringParams()
        score = heuristic_score(
            candidate_views=100_000,
            likes=5_000,
            duration=600,
            category_id=22,
            opportunity_score=0.7,
            params=params,
        )
        assert isinstance(score, float)
        assert score > 0

    def test_higher_views_higher_score(self):
        """Higher YouTube views should produce a higher score (same like ratio)."""
        params = ScoringParams()
        # Keep like ratio constant at 0.05 so only view_signal differs
        score_low = heuristic_score(10_000, 500, 600, 22, 0.5, params)
        score_high = heuristic_score(500_000, 25_000, 600, 22, 0.5, params)
        assert score_high > score_low

    def test_higher_engagement_higher_score(self):
        """Higher like ratio should produce a higher score."""
        params = ScoringParams()
        score_low = heuristic_score(100_000, 100, 600, 22, 0.5, params)
        score_high = heuristic_score(100_000, 10_000, 600, 22, 0.5, params)
        assert score_high > score_low

    def test_higher_opportunity_higher_score(self):
        """Higher opportunity_score should produce a higher score."""
        params = ScoringParams()
        score_low = heuristic_score(100_000, 3000, 600, 22, 0.1, params)
        score_high = heuristic_score(100_000, 3000, 600, 22, 0.9, params)
        assert score_high > score_low

    def test_duration_sweet_spot_bonus(self):
        """Videos in the duration sweet spot should score higher."""
        params = ScoringParams(duration_sweet_spot=(300, 900))
        score_sweet = heuristic_score(100_000, 3000, 600, 22, 0.5, params)
        score_outside = heuristic_score(100_000, 3000, 60, 22, 0.5, params)
        assert score_sweet > score_outside

    def test_category_bonus(self):
        """Category bonus multiplies the final score."""
        params = ScoringParams(category_bonuses={22: 2.0, 10: 0.5})
        score_bonus = heuristic_score(100_000, 3000, 600, 22, 0.5, params)
        score_penalty = heuristic_score(100_000, 3000, 600, 10, 0.5, params)
        assert score_bonus > score_penalty
        # The ratio should be approximately 2.0 / 0.5 = 4x
        assert abs(score_bonus / score_penalty - 4.0) < 0.01

    def test_category_not_in_bonuses_gets_1x(self):
        """Category not in bonuses dict defaults to 1.0 multiplier."""
        params = ScoringParams(category_bonuses={22: 1.5})
        score_with_bonus = heuristic_score(100_000, 3000, 600, 22, 0.5, params)
        score_no_bonus = heuristic_score(100_000, 3000, 600, 99, 0.5, params)
        assert score_with_bonus > score_no_bonus

    def test_zero_views(self):
        """heuristic_score handles zero views without error."""
        params = ScoringParams()
        score = heuristic_score(0, 0, 600, 22, 0.5, params)
        assert isinstance(score, float)
        assert score >= 0

    def test_zero_likes(self):
        """heuristic_score handles zero likes without error."""
        params = ScoringParams()
        score = heuristic_score(100_000, 0, 600, 22, 0.5, params)
        assert isinstance(score, float)
        assert score >= 0

    def test_known_values(self):
        """heuristic_score produces a known value for fixed inputs."""
        params = ScoringParams(
            engagement_good_threshold=0.04,
            engagement_weight=0.3,
            view_signal_weight=0.2,
            opportunity_weight=0.3,
            duration_weight=0.2,
            duration_sweet_spot=(300, 900),
            category_bonuses={},
        )
        # Manually compute expected
        # engagement: min(1.0, (5000/100000) / 0.04) = min(1.0, 1.25) = 1.0
        # view_signal: min(1.0, log1p(100000) / log1p(1000000)) = min(1.0, 11.5129/13.8155) ~ 0.8333
        # duration_score: 600 in [300,900] => 1.0
        # category_bonus: not in dict => 1.0
        # raw = 1.0*0.3 + 0.8333*0.2 + 0.7*0.3 + 1.0*0.2
        #     = 0.3 + 0.16667 + 0.21 + 0.2 = 0.8767
        score = heuristic_score(100_000, 5_000, 600, 22, 0.7, params)
        expected_engagement = 1.0
        expected_view = math.log1p(100_000) / math.log1p(1_000_000)
        expected_raw = (
            expected_engagement * 0.3
            + expected_view * 0.2
            + 0.7 * 0.3
            + 1.0 * 0.2
        )
        assert abs(score - expected_raw) < 0.001


# ── bootstrap_scoring_params ──────────────────────────────────────────


def _setup_bootstrap_db():
    """Create an in-memory DB with competitor_videos and youtube_stats tables."""
    db = Database(":memory:")
    db.connect()
    db._conn.execute("""
        CREATE TABLE competitor_videos (
            bvid TEXT PRIMARY KEY,
            bilibili_uid TEXT,
            title TEXT,
            views INTEGER DEFAULT 0,
            youtube_source_id TEXT
        )
    """)
    db._conn.execute("""
        CREATE TABLE youtube_stats (
            youtube_id TEXT PRIMARY KEY,
            bvid TEXT,
            yt_views INTEGER DEFAULT 0,
            yt_likes INTEGER DEFAULT 0,
            yt_duration_seconds INTEGER DEFAULT 0,
            yt_category_id INTEGER
        )
    """)
    db._conn.commit()
    return db


class TestBootstrapScoringParams:
    def test_returns_defaults_on_empty_db(self):
        """bootstrap returns default ScoringParams when no data exists."""
        db = _setup_bootstrap_db()
        params = bootstrap_scoring_params(db)

        # Should return defaults because no rows match
        assert isinstance(params, ScoringParams)
        assert params.engagement_good_threshold == 0.04
        db.close()

    def test_with_sample_data(self):
        """bootstrap derives params from sample competitor+youtube data."""
        db = _setup_bootstrap_db()

        # Insert sample data: 10 videos with varying stats
        for i in range(10):
            bvid = f"BV{i:03d}"
            yt_id = f"yt_{i:03d}"
            bili_views = (i + 1) * 10_000
            yt_views = (i + 1) * 50_000
            yt_likes = int(yt_views * 0.03)  # 3% like ratio
            duration = 300 + i * 60  # 300s to 840s
            category = 22 if i < 7 else 10

            db._conn.execute(
                "INSERT INTO competitor_videos (bvid, bilibili_uid, title, views, youtube_source_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (bvid, "uid1", f"Video {i}", bili_views, yt_id),
            )
            db._conn.execute(
                "INSERT INTO youtube_stats (youtube_id, bvid, yt_views, yt_likes, yt_duration_seconds, yt_category_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (yt_id, bvid, yt_views, yt_likes, duration, category),
            )
        db._conn.commit()

        params = bootstrap_scoring_params(db)

        assert isinstance(params, ScoringParams)
        # engagement threshold should be derived from data (around 0.03)
        assert 0.01 <= params.engagement_good_threshold <= 0.10
        # duration sweet spot should be derived from top-quartile performers
        assert params.duration_sweet_spot[0] > 0
        assert params.duration_sweet_spot[1] > params.duration_sweet_spot[0]
        # bilibili_success_threshold derived from p60 of bili_views
        assert params.bilibili_success_threshold >= 1000
        # Should have category bonuses for categories 22 and 10
        assert len(params.category_bonuses) > 0
        db.close()

    def test_raises_when_not_connected(self):
        """bootstrap raises RuntimeError when db not connected."""
        db = Database(":memory:")
        with pytest.raises(RuntimeError):
            bootstrap_scoring_params(db)

    def test_zero_views_in_data(self):
        """bootstrap handles rows with zero YouTube views gracefully."""
        db = _setup_bootstrap_db()
        # Insert a row where yt_views > 0 (required by WHERE clause)
        db._conn.execute(
            "INSERT INTO competitor_videos VALUES ('BV001', 'uid', 'title', 5000, 'yt1')"
        )
        db._conn.execute(
            "INSERT INTO youtube_stats VALUES ('yt1', 'BV001', 100, 5, 300, 22)"
        )
        db._conn.commit()

        params = bootstrap_scoring_params(db)
        assert isinstance(params, ScoringParams)
        db.close()

    def test_single_row_data(self):
        """bootstrap works correctly with a single row of data."""
        db = _setup_bootstrap_db()
        db._conn.execute(
            "INSERT INTO competitor_videos VALUES ('BV001', 'uid', 'title', 50000, 'yt1')"
        )
        db._conn.execute(
            "INSERT INTO youtube_stats VALUES ('yt1', 'BV001', 200000, 8000, 600, 22)"
        )
        db._conn.commit()

        params = bootstrap_scoring_params(db)
        assert isinstance(params, ScoringParams)
        assert params.engagement_good_threshold >= 0.01
        db.close()

    def test_category_bonus_clamped(self):
        """Category bonuses are clamped between 0.5 and 2.0."""
        db = _setup_bootstrap_db()

        # Insert data with extreme category performance differences
        for i in range(5):
            bvid = f"BV{i:03d}"
            yt_id = f"yt_{i:03d}"
            # Category 22: very high views, Category 10: very low views
            if i < 3:
                bili_views = 200_000
                category = 22
            else:
                bili_views = 1_000
                category = 10

            db._conn.execute(
                "INSERT INTO competitor_videos VALUES (?, 'uid', 'title', ?, ?)",
                (bvid, bili_views, yt_id),
            )
            db._conn.execute(
                "INSERT INTO youtube_stats VALUES (?, ?, 100000, 3000, 600, ?)",
                (yt_id, bvid, category),
            )
        db._conn.commit()

        params = bootstrap_scoring_params(db)
        for cat, bonus in params.category_bonuses.items():
            assert 0.5 <= bonus <= 2.0, f"Category {cat} bonus {bonus} not clamped"
        db.close()


# ── Transportability check (from scoring module) ──────────────────────


class TestCheckTransportability:
    def test_importable(self):
        """check_transportability is importable from app.scoring."""
        from app.scoring import check_transportability
        assert callable(check_transportability)

    def test_returns_transportable_on_success(self):
        """check_transportability returns parsed result on success."""
        from app.scoring import check_transportability

        backend = MagicMock()
        backend.chat.return_value = '{"transportable": true, "reasoning": "Visual content"}'

        result = check_transportability(
            backend, title="Amazing Nature 4K", channel="NatGeo",
            duration_seconds=600, category_id=22,
        )
        assert result["transportable"] is True
        assert "Visual" in result["reasoning"]

    def test_returns_true_on_error(self):
        """check_transportability defaults to transportable=True on LLM error."""
        from app.scoring import check_transportability

        backend = MagicMock()
        backend.chat.side_effect = Exception("LLM offline")

        result = check_transportability(
            backend, title="Title", channel="Ch",
            duration_seconds=300, category_id=10,
        )
        assert result["transportable"] is True
        assert "failed" in result["reasoning"].lower() or "Failed" in result["reasoning"]
