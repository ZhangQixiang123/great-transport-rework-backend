"""
Feature extraction for competitor video scoring.

Two modes:
  1. Regression: predict log(views) using LightGBM (or GPBoost mixed effects).
     Fixed effects use pre-upload + clickbait + YouTube + title embedding features.
     Optional random intercepts per channel for known-channel use cases.
  2. Classification: derived from regression predictions using percentile thresholds.

Pre-upload features (10):
  Content (5): duration, duration_bucket, title_length, title_has_number, description_length
  Time (4): publish_hour_sin, publish_hour_cos, publish_dow_sin, publish_dow_cos
  Source (1): has_youtube_source

Clickbait features (3):
  title_exclamation_count, title_question_count, title_caps_ratio

YouTube original stats features (7, when available):
  yt_log_views, yt_log_likes, yt_log_comments, yt_duration_seconds,
  yt_like_view_ratio, yt_comment_view_ratio, yt_category_id

Additional features (3):
  yt_tag_count, yt_upload_delay_days, yt_stats_imputed

Title embedding features (20):
  title_emb_0 .. title_emb_19 (PCA-reduced sentence-transformer embeddings)
"""
import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..db.database import CompetitorVideo, Database

logger = logging.getLogger(__name__)

N_EMBEDDING_DIMS = 20

LABEL_MAP = {"failed": 0, "standard": 1, "successful": 2, "viral": 3}
LABEL_NAMES = {v: k for k, v in LABEL_MAP.items()}

# Pre-upload features only (no post-upload metrics like views/likes/coins)
PRE_UPLOAD_FEATURES = [
    # Content
    "duration", "duration_bucket", "title_length", "title_has_number",
    "description_length",
    # Cyclical time encoding
    "publish_hour_sin", "publish_hour_cos",
    "publish_dow_sin", "publish_dow_cos",
    # Source
    "has_youtube_source",
]

# Clickbait signal features
CLICKBAIT_FEATURES = [
    "title_exclamation_count",
    "title_question_count",
    "title_caps_ratio",
]

# YouTube original stats features (from youtube_stats table)
YOUTUBE_FEATURES = [
    "yt_log_views", "yt_log_likes", "yt_log_comments",
    "yt_duration_seconds", "yt_like_view_ratio", "yt_comment_view_ratio",
    "yt_category_id",
]

# Additional features
ADDITIONAL_FEATURES = [
    "yt_tag_count",             # len(tags array) from youtube_stats
    "yt_upload_delay_days",     # days between YT publish and Bilibili publish
    "yt_stats_imputed",         # 1.0 if YT stats were imputed, 0.0 if real
]

# Title embedding features (PCA-reduced sentence-transformer)
EMBEDDING_FEATURES = [f"title_emb_{i}" for i in range(N_EMBEDDING_DIMS)]

# Full feature set: 10 + 3 + 7 + 3 + 20 = 43 features
FEATURE_NAMES = (PRE_UPLOAD_FEATURES + CLICKBAIT_FEATURES
                 + YOUTUBE_FEATURES + ADDITIONAL_FEATURES + EMBEDDING_FEATURES)

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


def _parse_yt_tags(yt_tags_str: Optional[str]) -> int:
    """Parse YouTube tags JSON string and return count."""
    if not yt_tags_str:
        return 0
    try:
        tags = json.loads(yt_tags_str)
        if isinstance(tags, list):
            return len(tags)
    except (json.JSONDecodeError, TypeError):
        pass
    return 0


def _parse_yt_published_at(yt_published_at: Optional[str]) -> Optional[datetime]:
    """Parse YouTube published_at timestamp string."""
    if not yt_published_at:
        return None
    try:
        return datetime.fromisoformat(yt_published_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def extract_features_single(
    video: CompetitorVideo,
    yt_stats: Optional[Dict] = None,
    yt_imputed: bool = False,
    title_embedding: Optional[np.ndarray] = None,
) -> Dict[str, float]:
    """Extract feature dictionary from a single CompetitorVideo.

    Args:
        video: CompetitorVideo record.
        yt_stats: Optional YouTube stats dict with keys like yt_views, yt_likes, etc.
        yt_imputed: Whether the YT stats are imputed (True) or real (False).
        title_embedding: Optional PCA-reduced title embedding array (N_EMBEDDING_DIMS,).
    """
    duration = max(video.duration, 0)
    publish_hour = video.publish_time.hour if video.publish_time else 12
    publish_dow = video.publish_time.weekday() if video.publish_time else 3

    # Cyclical time encoding
    hour_sin = math.sin(2 * math.pi * publish_hour / 24)
    hour_cos = math.cos(2 * math.pi * publish_hour / 24)
    dow_sin = math.sin(2 * math.pi * publish_dow / 7)
    dow_cos = math.cos(2 * math.pi * publish_dow / 7)

    # Clickbait signals (count both ASCII and Chinese punctuation)
    title = video.title
    exclamation_count = title.count("!") + title.count("\uff01")  # ! + !
    question_count = title.count("?") + title.count("\uff1f")     # ? + ?
    alpha_chars = sum(1 for c in title if c.isalpha())
    upper_chars = sum(1 for c in title if c.isupper())
    caps_ratio = upper_chars / alpha_chars if alpha_chars > 0 else 0.0

    features = {
        # Content features (pre-upload)
        "duration": float(duration),
        "duration_bucket": float(_duration_bucket(duration)),
        "title_length": float(len(title)),
        "title_has_number": 1.0 if re.search(r"\d", title) else 0.0,
        "description_length": float(len(video.description)),
        # Cyclical time features
        "publish_hour_sin": hour_sin,
        "publish_hour_cos": hour_cos,
        "publish_dow_sin": dow_sin,
        "publish_dow_cos": dow_cos,
        # Source feature
        "has_youtube_source": 1.0 if video.youtube_source_id else 0.0,
        # Clickbait features
        "title_exclamation_count": float(exclamation_count),
        "title_question_count": float(question_count),
        "title_caps_ratio": caps_ratio,
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

        # Additional YT features
        features["yt_tag_count"] = float(yt_stats.get("yt_tag_count", _parse_yt_tags(yt_stats.get("yt_tags"))))
        yt_pub = _parse_yt_published_at(yt_stats.get("yt_published_at"))
        if yt_pub and video.publish_time:
            # Make both naive for comparison
            v_time = video.publish_time.replace(tzinfo=None) if video.publish_time.tzinfo else video.publish_time
            y_time = yt_pub.replace(tzinfo=None) if yt_pub.tzinfo else yt_pub
            delay = (v_time - y_time).days
            features["yt_upload_delay_days"] = float(max(delay, 0))
        else:
            features["yt_upload_delay_days"] = 0.0
    else:
        # Fill with 0 when no YouTube stats available
        for feat in YOUTUBE_FEATURES:
            features[feat] = 0.0
        features["yt_tag_count"] = 0.0
        features["yt_upload_delay_days"] = 0.0

    # Imputation flag
    features["yt_stats_imputed"] = 1.0 if yt_imputed else 0.0

    # Title embedding features
    if title_embedding is not None and len(title_embedding) == N_EMBEDDING_DIMS:
        for i in range(N_EMBEDDING_DIMS):
            features[f"title_emb_{i}"] = float(title_embedding[i])
    else:
        for i in range(N_EMBEDDING_DIMS):
            features[f"title_emb_{i}"] = 0.0

    return features


def load_embedding_map(embeddings_path: str = "models/title_embeddings.npz") -> Dict[str, np.ndarray]:
    """Load pre-computed title embeddings from .npz file.

    Returns:
        Dict mapping bvid -> PCA-reduced embedding array.
    """
    if not os.path.exists(embeddings_path):
        logger.warning("Embeddings file not found: %s", embeddings_path)
        return {}
    data = np.load(embeddings_path)
    bvids = data["bvids"]
    embeddings = data["embeddings"]
    return {str(bvid): embeddings[i] for i, bvid in enumerate(bvids)}


def extract_features_dataframe(
    videos: List[CompetitorVideo],
    yt_stats_map: Optional[Dict[str, Dict]] = None,
    yt_imputation_stats: Optional[Dict] = None,
    embedding_map: Optional[Dict[str, np.ndarray]] = None,
) -> pd.DataFrame:
    """Extract features from a list of CompetitorVideo into a DataFrame.

    Args:
        videos: List of CompetitorVideo records.
        yt_stats_map: Optional mapping of bvid -> youtube stats dict.
        yt_imputation_stats: Optional imputation stats for missing YT data.
        embedding_map: Optional mapping of bvid -> title embedding array.
    """
    rows = []
    for v in videos:
        # Determine YouTube stats (real or imputed)
        yt = yt_stats_map.get(v.bvid) if yt_stats_map else None
        yt_imputed = False
        if yt is None and yt_imputation_stats:
            ch_imp = yt_imputation_stats.get("per_channel", {}).get(v.bilibili_uid)
            if ch_imp:
                yt = dict(ch_imp)
            else:
                yt = dict(yt_imputation_stats.get("global", {}))
            if yt:
                yt_imputed = True

        emb = embedding_map.get(v.bvid) if embedding_map else None

        rows.append(extract_features_single(
            v, yt_stats=yt, yt_imputed=yt_imputed, title_embedding=emb,
        ))
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


def compute_yt_imputation_stats(
    videos: List[CompetitorVideo],
    yt_stats_map: Dict[str, Dict],
) -> Dict:
    """Compute per-channel and global average YouTube stats for imputation.

    Only uses videos that have real (non-imputed) YouTube stats.

    Args:
        videos: List of CompetitorVideo records.
        yt_stats_map: Mapping of bvid -> youtube stats dict (real stats only).

    Returns:
        Dict with "per_channel" and "global" keys containing average stats.
    """
    from collections import defaultdict

    yt_fields = ["yt_views", "yt_likes", "yt_comments", "yt_duration_seconds", "yt_category_id"]

    # Accumulate per channel
    ch_sums = defaultdict(lambda: defaultdict(float))
    ch_counts = defaultdict(int)
    ch_tag_sums = defaultdict(float)

    global_sums = defaultdict(float)
    global_tag_sum = 0.0
    global_count = 0

    for v in videos:
        yt = yt_stats_map.get(v.bvid)
        if yt is None:
            continue

        uid = v.bilibili_uid
        ch_counts[uid] += 1
        global_count += 1

        for f in yt_fields:
            val = float(yt.get(f, 0))
            ch_sums[uid][f] += val
            global_sums[f] += val

        tag_count = float(_parse_yt_tags(yt.get("yt_tags")))
        ch_tag_sums[uid] += tag_count
        global_tag_sum += tag_count

    # Compute averages
    per_channel = {}
    for uid in ch_counts:
        avg = {}
        for f in yt_fields:
            avg[f] = ch_sums[uid][f] / ch_counts[uid]
        avg["yt_tag_count"] = ch_tag_sums[uid] / ch_counts[uid]
        per_channel[uid] = avg

    global_avg = {}
    if global_count > 0:
        for f in yt_fields:
            global_avg[f] = global_sums[f] / global_count
        global_avg["yt_tag_count"] = global_tag_sum / global_count

    return {"per_channel": per_channel, "global": global_avg}


def _load_youtube_stats_map(db_path: str) -> Dict[str, Dict]:
    """Load YouTube stats from the youtube_stats table, keyed by bvid.

    Only loads 'source_id' matches (reliable).
    Also loads yt_tags and yt_published_at for additional features.
    """
    yt_map = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT bvid, yt_views, yt_likes, yt_comments,
                   yt_duration_seconds, yt_category_id,
                   yt_tags, yt_published_at
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
                "yt_tags": r["yt_tags"],
                "yt_published_at": r["yt_published_at"],
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


def load_regression_data(
    db: Database,
) -> Tuple[List[CompetitorVideo], np.ndarray, Dict[str, Dict]]:
    """Load training data for regression (predict log_views).

    Returns all competitor videos (labeled or not) that have views > 0.

    Returns:
        Tuple of (videos, raw_targets, yt_stats_map).
        Trainer is responsible for computing imputation stats
        and building the feature DataFrame.
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
        return [], np.array([], dtype=np.float64), {}

    targets = extract_regression_target(videos)
    yt_map = _load_youtube_stats_map(db.connection_string)
    return videos, targets, yt_map
