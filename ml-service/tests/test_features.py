"""Tests for feature extraction."""
import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from app.db.database import CompetitorVideo
from app.training.features import (
    FEATURE_NAMES,
    PRE_UPLOAD_FEATURES,
    YOUTUBE_FEATURES,
    LABEL_MAP,
    _duration_bucket,
    _safe_ratio,
    extract_features_dataframe,
    extract_features_single,
    extract_labels,
    extract_regression_target,
)


def _make_video(**kwargs):
    """Create a CompetitorVideo with sensible defaults."""
    defaults = dict(
        bvid="BV1test",
        bilibili_uid="12345",
        title="Test Video Title 123",
        description="A test description",
        duration=300,
        views=10000,
        likes=500,
        coins=100,
        favorites=200,
        shares=50,
        danmaku=80,
        comments=30,
        publish_time=datetime(2024, 6, 15, 14, 30),
        collected_at=datetime(2024, 6, 16, 10, 0),
        youtube_source_id="dQw4w9WgXcQ",
        label="successful",
    )
    defaults.update(kwargs)
    return CompetitorVideo(**defaults)


class TestDurationBucket:
    def test_short(self):
        assert _duration_bucket(0) == 0
        assert _duration_bucket(60) == 0
        assert _duration_bucket(179) == 0

    def test_medium(self):
        assert _duration_bucket(180) == 1
        assert _duration_bucket(400) == 1
        assert _duration_bucket(599) == 1

    def test_long(self):
        assert _duration_bucket(600) == 2
        assert _duration_bucket(1000) == 2
        assert _duration_bucket(1799) == 2

    def test_very_long(self):
        assert _duration_bucket(1800) == 3
        assert _duration_bucket(7200) == 3


class TestSafeRatio:
    def test_normal(self):
        assert _safe_ratio(10, 100) == 0.1

    def test_zero_denominator(self):
        assert _safe_ratio(10, 0) == 0.0

    def test_zero_numerator(self):
        assert _safe_ratio(0, 100) == 0.0


class TestExtractFeaturesSingle:
    def test_returns_all_features(self):
        video = _make_video()
        features = extract_features_single(video)
        assert set(features.keys()) == set(FEATURE_NAMES)

    def test_feature_count(self):
        video = _make_video()
        features = extract_features_single(video)
        assert len(features) == len(FEATURE_NAMES)

    def test_pre_upload_features(self):
        video = _make_video(duration=300, title="Hello World 42")
        features = extract_features_single(video)
        assert features["duration"] == 300.0
        assert features["duration_bucket"] == 1.0  # medium
        assert features["title_length"] == 14.0
        assert features["title_has_number"] == 1.0

    def test_content_features(self):
        video = _make_video(title="Hello World 42", duration=300)
        features = extract_features_single(video)
        assert features["title_length"] == 14.0
        assert features["title_has_number"] == 1.0
        assert features["duration_bucket"] == 1.0  # medium (3-10 min)

    def test_title_no_number(self):
        video = _make_video(title="No Numbers Here")
        features = extract_features_single(video)
        assert features["title_has_number"] == 0.0

    def test_time_features(self):
        # June 15 2024 is a Saturday (weekday=5), hour=14
        video = _make_video(publish_time=datetime(2024, 6, 15, 14, 30))
        features = extract_features_single(video)
        assert features["publish_hour"] == 14.0
        assert features["publish_day_of_week"] == 5.0  # Saturday

    def test_no_publish_time_defaults(self):
        video = _make_video(publish_time=None)
        features = extract_features_single(video)
        assert features["publish_hour"] == 12.0
        assert features["publish_day_of_week"] == 3.0

    def test_youtube_source(self):
        video = _make_video(youtube_source_id="abc123")
        features = extract_features_single(video)
        assert features["has_youtube_source"] == 1.0

    def test_no_youtube_source(self):
        video = _make_video(youtube_source_id=None)
        features = extract_features_single(video)
        assert features["has_youtube_source"] == 0.0

    def test_youtube_features_without_stats(self):
        """Without yt_stats, YouTube features are 0."""
        video = _make_video()
        features = extract_features_single(video)
        for feat in YOUTUBE_FEATURES:
            assert features[feat] == 0.0

    def test_youtube_features_with_stats(self):
        """With yt_stats, YouTube features are populated."""
        video = _make_video()
        yt_stats = {
            "yt_views": 100000,
            "yt_likes": 5000,
            "yt_comments": 200,
            "yt_duration_seconds": 600,
            "yt_category_id": 22,
        }
        features = extract_features_single(video, yt_stats=yt_stats)
        assert features["yt_log_views"] == pytest.approx(math.log1p(100000))
        assert features["yt_log_likes"] == pytest.approx(math.log1p(5000))
        assert features["yt_log_comments"] == pytest.approx(math.log1p(200))
        assert features["yt_duration_seconds"] == 600.0
        assert features["yt_like_view_ratio"] == pytest.approx(5000 / 100000)
        assert features["yt_comment_view_ratio"] == pytest.approx(200 / 100000)
        assert features["yt_category_id"] == 22.0


class TestExtractFeaturesDataframe:
    def test_returns_dataframe(self):
        videos = [_make_video(bvid=f"BV{i}") for i in range(5)]
        df = extract_features_dataframe(videos)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 5
        assert list(df.columns) == FEATURE_NAMES

    def test_empty_list(self):
        df = extract_features_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
        assert list(df.columns) == FEATURE_NAMES

    def test_with_yt_stats_map(self):
        videos = [_make_video(bvid="BV1"), _make_video(bvid="BV2")]
        yt_map = {
            "BV1": {"yt_views": 50000, "yt_likes": 1000, "yt_comments": 100,
                     "yt_duration_seconds": 300, "yt_category_id": 10},
        }
        df = extract_features_dataframe(videos, yt_stats_map=yt_map)
        # BV1 should have YouTube features, BV2 should have zeros
        assert df.loc[0, "yt_log_views"] > 0
        assert df.loc[1, "yt_log_views"] == 0.0


class TestExtractRegressionTarget:
    def test_returns_log_views(self):
        videos = [
            _make_video(bvid="BV1", views=1000),
            _make_video(bvid="BV2", views=10000),
        ]
        targets = extract_regression_target(videos)
        assert len(targets) == 2
        assert targets[0] == pytest.approx(math.log1p(1000))
        assert targets[1] == pytest.approx(math.log1p(10000))

    def test_zero_views(self):
        videos = [_make_video(views=0)]
        targets = extract_regression_target(videos)
        assert targets[0] == 0.0


class TestExtractLabels:
    def test_valid_labels(self):
        videos = [
            _make_video(bvid="BV1", label="failed"),
            _make_video(bvid="BV2", label="standard"),
            _make_video(bvid="BV3", label="successful"),
            _make_video(bvid="BV4", label="viral"),
        ]
        labels = extract_labels(videos)
        np.testing.assert_array_equal(labels, [0, 1, 2, 3])
        assert labels.dtype == np.int32

    def test_invalid_label_raises(self):
        videos = [_make_video(bvid="BV1", label="unknown")]
        with pytest.raises(ValueError, match="invalid label"):
            extract_labels(videos)

    def test_none_label_raises(self):
        videos = [_make_video(bvid="BV1", label=None)]
        with pytest.raises(ValueError, match="invalid label"):
            extract_labels(videos)

    def test_label_map_consistency(self):
        assert LABEL_MAP == {"failed": 0, "standard": 1, "successful": 2, "viral": 3}


class TestFeatureNames:
    def test_total_feature_count(self):
        assert len(FEATURE_NAMES) == len(PRE_UPLOAD_FEATURES) + len(YOUTUBE_FEATURES)

    def test_no_circular_features(self):
        """Verify no post-upload metrics in feature list."""
        circular = {"views", "likes", "coins", "favorites", "shares",
                    "danmaku", "comments", "engagement_rate", "like_ratio",
                    "coin_ratio", "favorite_ratio", "share_ratio",
                    "danmaku_ratio", "log_views"}
        assert not circular.intersection(set(FEATURE_NAMES))
