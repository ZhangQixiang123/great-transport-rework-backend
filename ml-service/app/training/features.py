"""
Feature extraction for competitor video scoring.

Two modes:
  1. Regression: predict log(views) from pre-upload features + YouTube original stats.
     Only uses features available BEFORE uploading to Bilibili.
  2. Classification: derived from regression predictions using percentile thresholds.

Pre-upload features (8):
  Content (5): duration, duration_bucket, title_length, title_has_number, description_length
  Time (2): publish_hour, publish_day_of_week
  Source (1): has_youtube_source

YouTube original stats features (7, when available):
  yt_log_views, yt_log_likes, yt_log_comments, yt_duration_seconds,
  yt_like_view_ratio, yt_comment_view_ratio, yt_category_id
"""
import math
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..db.database import CompetitorVideo, Database

LABEL_MAP = {"failed": 0, "standard": 1, "successful": 2, "viral": 3}
LABEL_NAMES = {v: k for k, v in LABEL_MAP.items()}

# Pre-upload features only (no post-upload metrics like views/likes/coins)
PRE_UPLOAD_FEATURES = [
    # Content
    "duration", "duration_bucket", "title_length", "title_has_number",
    "description_length",
    # Time
    "publish_hour", "publish_day_of_week",
    # Source
    "has_youtube_source",
]

# YouTube original stats features (from youtube_stats table)
YOUTUBE_FEATURES = [
    "yt_log_views", "yt_log_likes", "yt_log_comments",
    "yt_duration_seconds", "yt_like_view_ratio", "yt_comment_view_ratio",
    "yt_category_id",
]

# Full feature set for regression model
FEATURE_NAMES = PRE_UPLOAD_FEATURES + YOUTUBE_FEATURES

# Legacy: keep old classification features for backward compat with tests
LEGACY_FEATURE_NAMES = [
    "views", "likes", "coins", "favorites", "shares", "danmaku", "comments",
    "engagement_rate", "like_ratio", "coin_ratio", "favorite_ratio",
    "share_ratio", "danmaku_ratio",
    "duration", "duration_bucket", "title_length", "title_has_number",
    "description_length",
    "publish_hour", "publish_day_of_week", "has_youtube_source",
    "log_views",
]


def _duration_bucket(duration: int) -> int:
    """Categorize duration into buckets.

    0: short (<3 min)
    1: medium (3-10 min)
    2: long (10-30 min)
    3: very long (>30 min)
    """
    if duration < 180:
        return 0
    elif duration < 600:
        return 1
    elif duration < 1800:
        return 2
    else:
        return 3


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Compute ratio safely, returning 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return numerator / denominator


def extract_features_single(video: CompetitorVideo, yt_stats: Optional[Dict] = None) -> Dict[str, float]:
    """Extract feature dictionary from a single CompetitorVideo.

    Args:
        video: CompetitorVideo record.
        yt_stats: Optional YouTube stats dict with keys like yt_views, yt_likes, etc.
    """
    duration = max(video.duration, 0)
    publish_hour = video.publish_time.hour if video.publish_time else 12
    publish_dow = video.publish_time.weekday() if video.publish_time else 3

    features = {
        # Content features (pre-upload)
        "duration": float(duration),
        "duration_bucket": float(_duration_bucket(duration)),
        "title_length": float(len(video.title)),
        "title_has_number": 1.0 if re.search(r"\d", video.title) else 0.0,
        "description_length": float(len(video.description)),
        # Time features (pre-upload)
        "publish_hour": float(publish_hour),
        "publish_day_of_week": float(publish_dow),
        # Source feature
        "has_youtube_source": 1.0 if video.youtube_source_id else 0.0,
    }

    # YouTube original stats features
    if yt_stats:
        yt_views = max(int(yt_stats.get("yt_views", 0)), 0)
        yt_likes = max(int(yt_stats.get("yt_likes", 0)), 0)
        yt_comments = max(int(yt_stats.get("yt_comments", 0)), 0)
        yt_duration = max(int(yt_stats.get("yt_duration_seconds", 0)), 0)
        yt_category = int(yt_stats.get("yt_category_id", 0))

        features["yt_log_views"] = math.log1p(yt_views)
        features["yt_log_likes"] = math.log1p(yt_likes)
        features["yt_log_comments"] = math.log1p(yt_comments)
        features["yt_duration_seconds"] = float(yt_duration)
        features["yt_like_view_ratio"] = _safe_ratio(yt_likes, yt_views)
        features["yt_comment_view_ratio"] = _safe_ratio(yt_comments, yt_views)
        features["yt_category_id"] = float(yt_category)
    else:
        # Fill with 0 when no YouTube stats available
        for feat in YOUTUBE_FEATURES:
            features[feat] = 0.0

    return features


def extract_features_dataframe(
    videos: List[CompetitorVideo],
    yt_stats_map: Optional[Dict[str, Dict]] = None,
) -> pd.DataFrame:
    """Extract features from a list of CompetitorVideo into a DataFrame.

    Args:
        videos: List of CompetitorVideo records.
        yt_stats_map: Optional mapping of bvid -> youtube stats dict.
    """
    rows = []
    for v in videos:
        yt = yt_stats_map.get(v.bvid) if yt_stats_map else None
        rows.append(extract_features_single(v, yt_stats=yt))
    df = pd.DataFrame(rows, columns=FEATURE_NAMES)
    return df


def extract_regression_target(videos: List[CompetitorVideo]) -> np.ndarray:
    """Extract log(views) as regression target."""
    return np.array([math.log1p(max(v.views, 0)) for v in videos], dtype=np.float64)


def extract_labels(videos: List[CompetitorVideo]) -> np.ndarray:
    """Extract numeric labels from videos.

    Raises:
        ValueError: If any video has an unknown or missing label.
    """
    labels = []
    for v in videos:
        if v.label is None or v.label not in LABEL_MAP:
            raise ValueError(f"Video {v.bvid} has invalid label: {v.label!r}")
        labels.append(LABEL_MAP[v.label])
    return np.array(labels, dtype=np.int32)


def _load_youtube_stats_map(db_path: str) -> Dict[str, Dict]:
    """Load YouTube stats from the youtube_stats table, keyed by bvid.

    Only loads 'source_id' matches (reliable).
    """
    yt_map = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT bvid, yt_views, yt_likes, yt_comments,
                   yt_duration_seconds, yt_category_id
            FROM youtube_stats
            WHERE match_method = 'source_id'
        """).fetchall()
        for r in rows:
            yt_map[r["bvid"]] = {
                "yt_views": r["yt_views"],
                "yt_likes": r["yt_likes"],
                "yt_comments": r["yt_comments"],
                "yt_duration_seconds": r["yt_duration_seconds"],
                "yt_category_id": r["yt_category_id"],
            }
        conn.close()
    except Exception:
        pass
    return yt_map


def load_training_data(db: Database) -> Tuple[pd.DataFrame, np.ndarray, List[str]]:
    """Load training data from the database (classification mode, legacy).

    Returns:
        Tuple of (features_df, labels_array, feature_names).
    """
    videos = db.get_labeled_competitor_videos()
    if not videos:
        return pd.DataFrame(columns=FEATURE_NAMES), np.array([], dtype=np.int32), FEATURE_NAMES

    yt_map = _load_youtube_stats_map(db.connection_string)
    features = extract_features_dataframe(videos, yt_stats_map=yt_map)
    labels = extract_labels(videos)
    return features, labels, FEATURE_NAMES


def load_regression_data(db: Database) -> Tuple[pd.DataFrame, np.ndarray, List[str], List[CompetitorVideo]]:
    """Load training data for regression (predict log_views).

    Returns all competitor videos (labeled or not) that have views > 0.

    Returns:
        Tuple of (features_df, targets_array, feature_names, videos).
    """
    if not db._conn:
        raise RuntimeError("Database not connected")

    # Get ALL videos with views > 0 (not just labeled ones)
    cursor = db._conn.execute("""
        SELECT bvid, bilibili_uid, title, description, duration, views, likes, coins,
               favorites, shares, danmaku, comments, publish_time, collected_at,
               youtube_source_id, label
        FROM competitor_videos
        WHERE views > 0
        ORDER BY publish_time ASC
    """)

    from datetime import datetime
    videos = []
    for row in cursor.fetchall():
        publish_time = row["publish_time"]
        if publish_time and isinstance(publish_time, str):
            publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))
        collected_at = row["collected_at"]
        if isinstance(collected_at, str):
            collected_at = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
        videos.append(CompetitorVideo(
            bvid=row["bvid"], bilibili_uid=row["bilibili_uid"],
            title=row["title"] or "", description=row["description"] or "",
            duration=row["duration"] or 0, views=row["views"] or 0,
            likes=row["likes"] or 0, coins=row["coins"] or 0,
            favorites=row["favorites"] or 0, shares=row["shares"] or 0,
            danmaku=row["danmaku"] or 0, comments=row["comments"] or 0,
            publish_time=publish_time, collected_at=collected_at,
            youtube_source_id=row["youtube_source_id"], label=row["label"],
        ))

    if not videos:
        return pd.DataFrame(columns=FEATURE_NAMES), np.array([], dtype=np.float64), FEATURE_NAMES, []

    yt_map = _load_youtube_stats_map(db.connection_string)
    features = extract_features_dataframe(videos, yt_stats_map=yt_map)
    targets = extract_regression_target(videos)
    return features, targets, FEATURE_NAMES, videos
