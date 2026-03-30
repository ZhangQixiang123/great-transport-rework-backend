"""Re-export from _shared (backward compatibility)."""
from app.personas._shared.scoring import ScoringParams, heuristic_score

__all__ = ["ScoringParams", "heuristic_score"]


def bootstrap_scoring_params(db, source: str = "competitor") -> ScoringParams:
    """Derive scoring parameters from historical transport data.

    Kept here for backward compatibility with bootstrap.py.
    """
    import logging
    from typing import Dict

    logger = logging.getLogger(__name__)

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

    def percentile(values, p):
        if not values:
            return 0
        sorted_v = sorted(values)
        k = (len(sorted_v) - 1) * (p / 100.0)
        f = int(k)
        c = f + 1
        if c >= len(sorted_v):
            return sorted_v[f]
        return sorted_v[f] + (k - f) * (sorted_v[c] - sorted_v[f])

    bili_views = [r["bili_views"] for r in rows]
    success_threshold = percentile(bili_views, 60)

    like_ratios = []
    for r in rows:
        if r["yt_views"] > 0:
            like_ratios.append(r["yt_likes"] / r["yt_views"])
    engagement_threshold = percentile(like_ratios, 75) if like_ratios else 0.04

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
