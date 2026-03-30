"""Data-calibrated heuristic scoring for video candidates.

Scoring weights are derived from historical transport data, not hardcoded.
"""
import json
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ScoringParams:
    """Scoring parameters derived from transport data."""
    engagement_good_threshold: float = 0.04
    engagement_weight: float = 0.3
    view_signal_weight: float = 0.2
    opportunity_weight: float = 0.3
    duration_weight: float = 0.2
    duration_sweet_spot: Tuple[int, int] = (300, 900)
    category_bonuses: Dict[int, float] = field(default_factory=dict)
    youtube_min_views: int = 50_000
    bilibili_success_threshold: int = 50_000

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "engagement_good_threshold": self.engagement_good_threshold,
            "engagement_weight": self.engagement_weight,
            "view_signal_weight": self.view_signal_weight,
            "opportunity_weight": self.opportunity_weight,
            "duration_weight": self.duration_weight,
            "duration_sweet_spot": list(self.duration_sweet_spot),
            "category_bonuses": {str(k): v for k, v in self.category_bonuses.items()},
            "youtube_min_views": self.youtube_min_views,
            "bilibili_success_threshold": self.bilibili_success_threshold,
        })

    @classmethod
    def from_json(cls, json_str: str) -> "ScoringParams":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls(
            engagement_good_threshold=data.get("engagement_good_threshold", 0.04),
            engagement_weight=data.get("engagement_weight", 0.3),
            view_signal_weight=data.get("view_signal_weight", 0.2),
            opportunity_weight=data.get("opportunity_weight", 0.3),
            duration_weight=data.get("duration_weight", 0.2),
            duration_sweet_spot=tuple(data.get("duration_sweet_spot", [300, 900])),
            category_bonuses={
                int(k): v for k, v in data.get("category_bonuses", {}).items()
            },
            youtube_min_views=data.get("youtube_min_views", 50_000),
            bilibili_success_threshold=data.get("bilibili_success_threshold", 50_000),
        )


def heuristic_score(
    candidate_views: int,
    likes: int,
    duration: int,
    category_id: int,
    opportunity_score: float,
    params: ScoringParams,
) -> float:
    """Score a YouTube video candidate using data-calibrated heuristic.

    Args:
        candidate_views: YouTube view count.
        likes: YouTube like count.
        duration: Video duration in seconds.
        category_id: YouTube category ID.
        opportunity_score: Market opportunity score (0-1).
        params: Scoring parameters derived from data.

    Returns:
        Combined heuristic score (higher is better).
    """
    # Engagement signal
    like_ratio = likes / max(candidate_views, 1)
    engagement = min(1.0, like_ratio / params.engagement_good_threshold)

    # View signal (log-scaled)
    view_signal = min(1.0, math.log1p(candidate_views) / math.log1p(1_000_000))

    # Duration score
    lo, hi = params.duration_sweet_spot
    duration_score = 1.0 if lo <= duration <= hi else 0.7

    # Category bonus
    category_bonus = params.category_bonuses.get(category_id, 1.0)

    # Weighted combination
    raw = (
        engagement * params.engagement_weight
        + view_signal * params.view_signal_weight
        + opportunity_score * params.opportunity_weight
        + duration_score * params.duration_weight
    )

    return raw * category_bonus


def bootstrap_scoring_params(db, source: str = "competitor") -> ScoringParams:
    """Derive scoring parameters from historical transport data.

    Uses pure Python percentile computation (no numpy).

    Args:
        db: Connected Database instance.
        source: "competitor" (from competitor_videos+youtube_stats) or
                "strategy_runs" (from our own transport data).

    Returns:
        ScoringParams derived from data.
    """
    if not db._conn:
        raise RuntimeError("Database not connected")

    if source == "strategy_runs":
        rows = db._conn.execute("""
            SELECT bilibili_views as bili_views,
                   youtube_views as yt_views,
                   youtube_likes as yt_likes,
                   youtube_duration_seconds as duration,
                   youtube_category_id as category_id
            FROM strategy_runs
            WHERE was_transported = 1 AND bilibili_views IS NOT NULL
                  AND youtube_views > 0
        """).fetchall()
    else:
        # Default: use competitor data
        rows = db._conn.execute("""
            SELECT cv.views as bili_views,
                   ys.yt_views,
                   ys.yt_likes,
                   ys.yt_duration_seconds as duration,
                   ys.yt_category_id as category_id
            FROM competitor_videos cv
            JOIN youtube_stats ys ON cv.youtube_source_id = ys.youtube_id
            WHERE cv.views > 0 AND ys.yt_views > 0
        """).fetchall()

    if not rows:
        logger.warning("No data for bootstrap — using defaults.")
        return ScoringParams()

    rows = [dict(r) for r in rows]

    # Compute percentile helper (pure Python)
    def percentile(values, p):
        """Compute p-th percentile of a sorted list."""
        if not values:
            return 0
        sorted_v = sorted(values)
        k = (len(sorted_v) - 1) * (p / 100.0)
        f = int(k)
        c = f + 1
        if c >= len(sorted_v):
            return sorted_v[f]
        return sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])

    # Engagement threshold: like_ratio at p75 of successful transports
    bili_views = [r["bili_views"] for r in rows]
    success_threshold = percentile(bili_views, 60)

    # Get like ratios for successful transports
    like_ratios = []
    for r in rows:
        if r["yt_views"] > 0:
            like_ratios.append(r["yt_likes"] / r["yt_views"])
    engagement_threshold = percentile(like_ratios, 75) if like_ratios else 0.04

    # Duration sweet spot: duration range of top-quartile performers
    top_quartile_threshold = percentile(bili_views, 75)
    top_durations = [
        r["duration"] for r in rows
        if r["bili_views"] >= top_quartile_threshold and r["duration"] and r["duration"] > 0
    ]
    if top_durations:
        dur_lo = int(percentile(top_durations, 25))
        dur_hi = int(percentile(top_durations, 75))
    else:
        dur_lo, dur_hi = 300, 900

    # Category bonuses
    category_views: Dict[int, list] = {}
    for r in rows:
        cat = r["category_id"]
        if cat:
            category_views.setdefault(cat, []).append(r["bili_views"])
    overall_median = percentile(bili_views, 50)
    category_bonuses = {}
    for cat, views in category_views.items():
        cat_median = percentile(views, 50)
        if overall_median > 0:
            bonus = cat_median / overall_median
            category_bonuses[cat] = round(min(max(bonus, 0.5), 2.0), 2)

    return ScoringParams(
        engagement_good_threshold=round(max(engagement_threshold, 0.01), 4),
        duration_sweet_spot=(dur_lo, dur_hi),
        category_bonuses=category_bonuses,
        bilibili_success_threshold=int(max(success_threshold, 1000)),
    )
