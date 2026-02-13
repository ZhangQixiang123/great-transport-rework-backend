"""Tests for feature extraction."""
import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from app.db.database import CompetitorVideo
from app.training.features import (
    ADDITIONAL_FEATURES,
    CLICKBAIT_FEATURES,
    EMBEDDING_FEATURES,
    FEATURE_NAMES,
    N_EMBEDDING_DIMS,
    PRE_UPLOAD_FEATURES,
    YOUTUBE_FEATURES,
    LABEL_MAP,
    _duration_bucket,
    _safe_ratio,
    compute_yt_imputation_stats,
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

    def test_time_features_cyclical(self):
        # June 15 2024 is a Saturday (weekday=5), hour=14
        video = _make_video(publish_time=datetime(2024, 6, 15, 14, 30))
        features = extract_features_single(video)
        assert features["publish_hour_sin"] == pytest.approx(math.sin(2 * math.pi * 14 / 24))
        assert features["publish_hour_cos"] == pytest.approx(math.cos(2 * math.pi * 14 / 24))
        assert features["publish_dow_sin"] == pytest.approx(math.sin(2 * math.pi * 5 / 7))
        assert features["publish_dow_cos"] == pytest.approx(math.cos(2 * math.pi * 5 / 7))

    def test_no_publish_time_defaults(self):
        video = _make_video(publish_time=None)
        features = extract_features_single(video)
        # Defaults: hour=12, dow=3
        assert features["publish_hour_sin"] == pytest.approx(math.sin(2 * math.pi * 12 / 24))
        assert features["publish_hour_cos"] == pytest.approx(math.cos(2 * math.pi * 12 / 24))
        assert features["publish_dow_sin"] == pytest.approx(math.sin(2 * math.pi * 3 / 7))
        assert features["publish_dow_cos"] == pytest.approx(math.cos(2 * math.pi * 3 / 7))

    def test_youtube_source(self):
        video = _make_video(youtube_source_id="abc123")
        features = extract_features_single(video)
        assert features["has_youtube_source"] == 1.0

    def test_no_youtube_source(self):
        video = _make_video(youtube_source_id=None)
        features = extract_features_single(video)
        assert features["has_youtube_source"] == 0.0

    def test_youtube_features_without_stats(self):
        """Without yt_stats, YouTube features and additional features are 0."""
        video = _make_video()
        features = extract_features_single(video)
        for feat in YOUTUBE_FEATURES:
            assert features[feat] == 0.0
        assert features["yt_tag_count"] == 0.0
        assert features["yt_upload_delay_days"] == 0.0
        assert features["yt_stats_imputed"] == 0.0

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

    def test_yt_tag_count(self):
        """yt_tag_count is extracted from yt_tags JSON."""
        video = _make_video()
        yt_stats = {
            "yt_views": 1000, "yt_likes": 50, "yt_comments": 5,
            "yt_duration_seconds": 300, "yt_category_id": 22,
            "yt_tags": '["tag1", "tag2", "tag3"]',
        }
        features = extract_features_single(video, yt_stats=yt_stats)
        assert features["yt_tag_count"] == 3.0

    def test_yt_tag_count_no_tags(self):
        """yt_tag_count is 0 when no tags."""
        video = _make_video()
        yt_stats = {
            "yt_views": 1000, "yt_likes": 50, "yt_comments": 5,
            "yt_duration_seconds": 300, "yt_category_id": 22,
        }
        features = extract_features_single(video, yt_stats=yt_stats)
        assert features["yt_tag_count"] == 0.0

    def test_yt_upload_delay(self):
        """yt_upload_delay_days computes days between YT and Bilibili publish."""
        video = _make_video(publish_time=datetime(2024, 6, 15, 14, 30))
        yt_stats = {
            "yt_views": 1000, "yt_likes": 50, "yt_comments": 5,
            "yt_duration_seconds": 300, "yt_category_id": 22,
            "yt_published_at": "2024-06-10T10:00:00Z",
        }
        features = extract_features_single(video, yt_stats=yt_stats)
        assert features["yt_upload_delay_days"] == 5.0

    def test_yt_upload_delay_negative_clamped(self):
        """Negative delay (YT published after Bilibili) is clamped to 0."""
        video = _make_video(publish_time=datetime(2024, 6, 10, 14, 30))
        yt_stats = {
            "yt_views": 1000, "yt_likes": 50, "yt_comments": 5,
            "yt_duration_seconds": 300, "yt_category_id": 22,
            "yt_published_at": "2024-06-15T10:00:00Z",
        }
        features = extract_features_single(video, yt_stats=yt_stats)
        assert features["yt_upload_delay_days"] == 0.0

    def test_yt_stats_imputed_flag(self):
        """yt_stats_imputed flag reflects imputation status."""
        video = _make_video()
        yt_stats = {
            "yt_views": 1000, "yt_likes": 50, "yt_comments": 5,
            "yt_duration_seconds": 300, "yt_category_id": 22,
        }
        # Real stats
        features = extract_features_single(video, yt_stats=yt_stats, yt_imputed=False)
        assert features["yt_stats_imputed"] == 0.0

        # Imputed stats
        features = extract_features_single(video, yt_stats=yt_stats, yt_imputed=True)
        assert features["yt_stats_imputed"] == 1.0

    def test_title_embedding_provided(self):
        """Title embedding features populated from array."""
        video = _make_video()
        emb = np.ones(N_EMBEDDING_DIMS) * 0.5
        features = extract_features_single(video, title_embedding=emb)
        for i in range(N_EMBEDDING_DIMS):
            assert features[f"title_emb_{i}"] == 0.5

    def test_title_embedding_defaults_to_zero(self):
        """Without embedding, title_emb features are 0."""
        video = _make_video()
        features = extract_features_single(video)
        for i in range(N_EMBEDDING_DIMS):
            assert features[f"title_emb_{i}"] == 0.0

    def test_clickbait_features(self):
        """Clickbait features count exclamation/question marks and caps ratio."""
        video = _make_video(title="WOW!! Amazing? YES")
        features = extract_features_single(video)
        assert features["title_exclamation_count"] == 2.0
        assert features["title_question_count"] == 1.0
        # "WOW", "A", "Y", "E", "S" = 10 alpha, 7 upper -> ratio = 7/10
        alpha = sum(1 for c in "WOW!! Amazing? YES" if c.isalpha())
        upper = sum(1 for c in "WOW!! Amazing? YES" if c.isupper())
        assert features["title_caps_ratio"] == pytest.approx(upper / alpha)

    def test_clickbait_chinese_punctuation(self):
        """Chinese exclamation/question marks are also counted."""
        video = _make_video(title="\uff01\uff1f test")
        features = extract_features_single(video)
        assert features["title_exclamation_count"] == 1.0
        assert features["title_question_count"] == 1.0

    def test_clickbait_no_alpha(self):
        """caps_ratio is 0 when title has no alphabetic characters."""
        video = _make_video(title="12345!!!")
        features = extract_features_single(video)
        assert features["title_caps_ratio"] == 0.0


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

    def test_with_imputation(self):
        """Videos without YT stats get imputed values when imputation stats provided."""
        videos = [
            _make_video(bvid="BV1", bilibili_uid="uid1"),
            _make_video(bvid="BV2", bilibili_uid="uid1"),
        ]
        yt_map = {
            "BV1": {"yt_views": 50000, "yt_likes": 1000, "yt_comments": 100,
                     "yt_duration_seconds": 300, "yt_category_id": 10},
        }
        yt_imp = {
            "per_channel": {"uid1": {"yt_views": 50000, "yt_likes": 1000,
                                      "yt_comments": 100, "yt_duration_seconds": 300,
                                      "yt_category_id": 10, "yt_tag_count": 5}},
            "global": {"yt_views": 30000, "yt_likes": 500, "yt_comments": 50,
                       "yt_duration_seconds": 250, "yt_category_id": 15, "yt_tag_count": 3},
        }
        df = extract_features_dataframe(videos, yt_stats_map=yt_map, yt_imputation_stats=yt_imp)
        # BV1 has real stats, BV2 gets imputed
        assert df.loc[0, "yt_stats_imputed"] == 0.0
        assert df.loc[1, "yt_stats_imputed"] == 1.0
        assert df.loc[1, "yt_log_views"] > 0  # imputed, not zero

    def test_with_embedding_map(self):
        """Embedding map populates title_emb features."""
        videos = [
            _make_video(bvid="BV1"),
            _make_video(bvid="BV2"),
        ]
        emb_map = {"BV1": np.ones(N_EMBEDDING_DIMS) * 0.3}
        df = extract_features_dataframe(videos, embedding_map=emb_map)
        assert df.loc[0, "title_emb_0"] == pytest.approx(0.3)
        assert df.loc[1, "title_emb_0"] == 0.0  # no embedding for BV2


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
        expected = (len(PRE_UPLOAD_FEATURES) + len(CLICKBAIT_FEATURES)
                    + len(YOUTUBE_FEATURES)
                    + len(ADDITIONAL_FEATURES) + len(EMBEDDING_FEATURES))
        assert len(FEATURE_NAMES) == expected
        assert len(FEATURE_NAMES) == 43

    def test_no_circular_features(self):
        """Verify no post-upload metrics in feature list."""
        circular = {"views", "likes", "coins", "favorites", "shares",
                    "danmaku", "comments", "engagement_rate", "like_ratio",
                    "coin_ratio", "favorite_ratio", "share_ratio",
                    "danmaku_ratio", "log_views"}
        assert not circular.intersection(set(FEATURE_NAMES))

    def test_no_channel_identity_features(self):
        """Verify no channel-identity-leaking features.

        channel_log_followers is allowed (it's a continuous size proxy,
        not a channel identifier).
        """
        leaky = {"channel_mean_log_views", "channel_follower_count", "channel_video_count"}
        assert not leaky.intersection(set(FEATURE_NAMES))

    def test_embedding_feature_count(self):
        assert len(EMBEDDING_FEATURES) == N_EMBEDDING_DIMS
        assert EMBEDDING_FEATURES[0] == "title_emb_0"
        assert EMBEDDING_FEATURES[-1] == f"title_emb_{N_EMBEDDING_DIMS - 1}"


class TestComputeYtImputationStats:
    def test_basic(self):
        videos = [
            _make_video(bvid="BV1", bilibili_uid="A"),
            _make_video(bvid="BV2", bilibili_uid="A"),
            _make_video(bvid="BV3", bilibili_uid="B"),
        ]
        yt_map = {
            "BV1": {"yt_views": 1000, "yt_likes": 50, "yt_comments": 10,
                     "yt_duration_seconds": 300, "yt_category_id": 22,
                     "yt_tags": '["a", "b"]'},
            "BV3": {"yt_views": 5000, "yt_likes": 200, "yt_comments": 30,
                     "yt_duration_seconds": 600, "yt_category_id": 10,
                     "yt_tags": '["x"]'},
        }
        stats = compute_yt_imputation_stats(videos, yt_map)

        # Per-channel: A has 1 video with stats, B has 1
        assert "A" in stats["per_channel"]
        assert "B" in stats["per_channel"]
        assert stats["per_channel"]["A"]["yt_views"] == 1000.0
        assert stats["per_channel"]["B"]["yt_views"] == 5000.0
        assert stats["per_channel"]["A"]["yt_tag_count"] == 2.0
        assert stats["per_channel"]["B"]["yt_tag_count"] == 1.0

        # Global: average of both
        assert stats["global"]["yt_views"] == 3000.0
        assert stats["global"]["yt_tag_count"] == 1.5

    def test_no_yt_stats(self):
        videos = [_make_video(bvid="BV1", bilibili_uid="A")]
        yt_map = {}
        stats = compute_yt_imputation_stats(videos, yt_map)
        assert stats["per_channel"] == {}
        assert stats["global"] == {}
