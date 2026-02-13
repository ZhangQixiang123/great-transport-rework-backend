"""Tests for the RankerModel inference wrapper (GPBoost)."""
import json
import math
import os
from datetime import datetime

import gpboost as gpb
import numpy as np
import pytest

from app.db.database import CompetitorVideo
from app.models.ranker import RankerModel
from app.training.features import FEATURE_NAMES, LABEL_NAMES


def _train_and_save(path, meta_path=None):
    """Train a tiny GPBoost model and save to path.

    If meta_path is provided, saves metadata with thresholds.
    """
    rng = np.random.RandomState(42)
    n = 80
    n_features = len(FEATURE_NAMES)
    X = rng.randn(n, n_features)
    # Groups: 4 channels
    groups = np.repeat(["ch_A", "ch_B", "ch_C", "ch_D"], n // 4)
    # Target: fixed effect from features + group offsets
    group_offsets = {"ch_A": 7.0, "ch_B": 8.0, "ch_C": 9.0, "ch_D": 10.0}
    y = X[:, 0] * 0.5 + np.array([group_offsets[g] for g in groups]) + rng.randn(n) * 0.3

    gp_model = gpb.GPModel(group_data=groups, likelihood="gaussian")
    data = gpb.Dataset(X, y)
    params = {
        "objective": "regression_l2",
        "num_leaves": 8,
        "learning_rate": 0.1,
        "verbose": -1,
    }
    model = gpb.train(params=params, train_set=data, gp_model=gp_model, num_boost_round=20)
    model.save_model(path)

    if meta_path:
        metadata = {
            "model_type": "gpboost_mixed_effects",
            "num_features": n_features,
            "feature_names": list(FEATURE_NAMES),
            "percentile_thresholds": {
                "p25": 7.0,
                "p75": 9.0,
                "p95": 10.5,
            },
            "yt_imputation_stats": {
                "per_channel": {
                    "12345": {
                        "yt_views": 50000, "yt_likes": 1000, "yt_comments": 100,
                        "yt_duration_seconds": 300, "yt_category_id": 22,
                        "yt_tag_count": 5,
                    },
                },
                "global": {
                    "yt_views": 30000, "yt_likes": 500, "yt_comments": 50,
                    "yt_duration_seconds": 250, "yt_category_id": 15,
                    "yt_tag_count": 3,
                },
            },
        }
        with open(meta_path, "w") as f:
            json.dump(metadata, f)

    return model


def _make_video(**kwargs):
    defaults = dict(
        bvid="BV1test",
        bilibili_uid="12345",
        title="Test Video 42",
        description="A test",
        duration=300,
        views=5000,
        likes=250,
        coins=50,
        favorites=100,
        shares=20,
        danmaku=40,
        comments=15,
        publish_time=datetime(2024, 6, 15, 14, 30),
        collected_at=datetime(2024, 6, 16, 10, 0),
        youtube_source_id=None,
        label="standard",
    )
    defaults.update(kwargs)
    return CompetitorVideo(**defaults)


class TestRankerModel:
    def test_load_from_file(self, tmp_path):
        """Model loads from a saved .json file."""
        model_path = str(tmp_path / "model.json")
        _train_and_save(model_path)

        ranker = RankerModel(model_path)
        assert ranker.model is not None
        assert ranker.model_path == model_path

    def test_file_not_found(self, tmp_path):
        """Raises FileNotFoundError for missing model."""
        with pytest.raises(FileNotFoundError):
            RankerModel(str(tmp_path / "nonexistent.json"))

    def test_predict_raw_shape(self, tmp_path):
        """predict_raw returns correct shape."""
        model_path = str(tmp_path / "model.json")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        X = np.random.randn(5, len(FEATURE_NAMES))
        groups = np.array(["ch_A", "ch_A", "ch_B", "ch_B", "new_ch"])
        pred = ranker.predict_raw(X, groups)
        assert pred.shape == (5,)

    def test_predict_raw_single(self, tmp_path):
        """predict_raw handles 1D input."""
        model_path = str(tmp_path / "model.json")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        X = np.random.randn(len(FEATURE_NAMES))
        groups = np.array(["ch_A"])
        pred = ranker.predict_raw(X, groups)
        assert pred.shape == (1,)

    def test_predict_video(self, tmp_path):
        """predict_video returns structured regression prediction."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)
        ranker = RankerModel(model_path, metadata_path=meta_path)

        video = _make_video(bilibili_uid="ch_A")
        result = ranker.predict_video(video)

        assert "label" in result
        assert result["label"] in LABEL_NAMES.values()
        assert "predicted_log_views" in result
        assert isinstance(result["predicted_log_views"], float)
        assert "predicted_views" in result
        assert isinstance(result["predicted_views"], float)
        assert result["predicted_views"] == pytest.approx(math.expm1(result["predicted_log_views"]))

    def test_predict_video_unseen_channel(self, tmp_path):
        """predict_video works for unseen channels (random effect = 0)."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)
        ranker = RankerModel(model_path, metadata_path=meta_path)

        video = _make_video(bilibili_uid="completely_new_channel")
        result = ranker.predict_video(video)

        assert "label" in result
        assert result["label"] in LABEL_NAMES.values()
        assert "predicted_log_views" in result

    def test_predict_video_with_imputation(self, tmp_path):
        """predict_video imputes YT stats from metadata when not provided."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)
        ranker = RankerModel(model_path, metadata_path=meta_path)

        video = _make_video(bilibili_uid="12345")
        result = ranker.predict_video(video)

        assert "label" in result
        assert "predicted_log_views" in result

    def test_predict_video_with_explicit_yt_stats(self, tmp_path):
        """predict_video uses provided YT stats over imputation."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)
        ranker = RankerModel(model_path, metadata_path=meta_path)

        video = _make_video(bilibili_uid="ch_A")
        yt_stats = {
            "yt_views": 100000, "yt_likes": 5000, "yt_comments": 200,
            "yt_duration_seconds": 600, "yt_category_id": 22,
        }
        result = ranker.predict_video(video, yt_stats=yt_stats)

        assert "label" in result
        assert "predicted_log_views" in result

    def test_load_latest(self, tmp_path):
        """load_latest finds the latest model."""
        model_dir = str(tmp_path / "models")
        os.makedirs(model_dir)

        latest_path = os.path.join(model_dir, "latest_model.json")
        _train_and_save(latest_path)

        ranker = RankerModel.load_latest(model_dir)
        assert ranker.model is not None

    def test_load_latest_not_found(self, tmp_path):
        """load_latest raises when no model exists."""
        model_dir = str(tmp_path / "empty_models")
        os.makedirs(model_dir)

        with pytest.raises(FileNotFoundError):
            RankerModel.load_latest(model_dir)

    def test_load_with_metadata(self, tmp_path):
        """Model loads metadata when available."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)

        ranker = RankerModel(model_path, metadata_path=meta_path)
        assert ranker.metadata is not None
        assert ranker._percentile_thresholds["p25"] == 7.0

    def test_classify_thresholds(self, tmp_path):
        """_classify uses percentile thresholds correctly."""
        model_path = str(tmp_path / "model.json")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path, meta_path=meta_path)
        ranker = RankerModel(model_path, metadata_path=meta_path)

        # Thresholds: p25=7.0, p75=9.0, p95=10.5
        assert ranker._classify(6.0) == "failed"
        assert ranker._classify(7.0) == "standard"
        assert ranker._classify(8.0) == "standard"
        assert ranker._classify(9.0) == "successful"
        assert ranker._classify(10.0) == "successful"
        assert ranker._classify(10.5) == "viral"
        assert ranker._classify(12.0) == "viral"

    def test_save_load_roundtrip(self, tmp_path):
        """Model produces same predictions after save/load."""
        model_path = str(tmp_path / "model.json")
        original_model = _train_and_save(model_path)

        rng = np.random.RandomState(99)
        X = rng.randn(10, len(FEATURE_NAMES))
        groups = np.repeat(["ch_A", "ch_B"], 5)
        original_pred = np.array(original_model.predict(data=X, group_data_pred=groups)["response_mean"])

        ranker = RankerModel(model_path)
        loaded_pred = ranker.predict_raw(X, groups)

        np.testing.assert_allclose(original_pred, loaded_pred, atol=1e-10)
