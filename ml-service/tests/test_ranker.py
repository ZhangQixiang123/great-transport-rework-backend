"""Tests for the RankerModel inference wrapper."""
import json
import os
from datetime import datetime

import lightgbm as lgb
import numpy as np
import pytest

from app.db.database import CompetitorVideo
from app.models.ranker import RankerModel
from app.training.features import FEATURE_NAMES, LABEL_NAMES


def _train_and_save(path):
    """Train a tiny model and save to path."""
    rng = np.random.RandomState(42)
    n = 80
    n_features = len(FEATURE_NAMES)
    X = rng.randn(n, n_features)
    y = np.array([i % 4 for i in range(n)])
    X[:, 0] = y * 2 + rng.randn(n) * 0.3

    train_data = lgb.Dataset(X, label=y, feature_name=FEATURE_NAMES)
    params = {
        "objective": "multiclass",
        "num_class": 4,
        "num_leaves": 8,
        "learning_rate": 0.1,
        "verbose": -1,
    }
    model = lgb.train(params, train_data, num_boost_round=20)
    model.save_model(path)
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
        """Model loads from a saved .txt file."""
        model_path = str(tmp_path / "model.txt")
        _train_and_save(model_path)

        ranker = RankerModel(model_path)
        assert ranker.model is not None
        assert ranker.model_path == model_path

    def test_file_not_found(self, tmp_path):
        """Raises FileNotFoundError for missing model."""
        with pytest.raises(FileNotFoundError):
            RankerModel(str(tmp_path / "nonexistent.txt"))

    def test_predict_proba_shape(self, tmp_path):
        """predict_proba returns correct shape."""
        model_path = str(tmp_path / "model.txt")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        X = np.random.randn(5, len(FEATURE_NAMES))
        proba = ranker.predict_proba(X)
        assert proba.shape == (5, 4)
        # Probabilities sum to ~1
        np.testing.assert_allclose(proba.sum(axis=1), 1.0, atol=1e-6)

    def test_predict_proba_single(self, tmp_path):
        """predict_proba handles 1D input."""
        model_path = str(tmp_path / "model.txt")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        X = np.random.randn(len(FEATURE_NAMES))
        proba = ranker.predict_proba(X)
        assert proba.shape == (1, 4)

    def test_predict_label(self, tmp_path):
        """predict_label returns string labels."""
        model_path = str(tmp_path / "model.txt")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        X = np.random.randn(3, len(FEATURE_NAMES))
        labels = ranker.predict_label(X)
        assert len(labels) == 3
        for label in labels:
            assert label in LABEL_NAMES.values()

    def test_predict_video(self, tmp_path):
        """predict_video returns structured prediction."""
        model_path = str(tmp_path / "model.txt")
        _train_and_save(model_path)
        ranker = RankerModel(model_path)

        video = _make_video()
        result = ranker.predict_video(video)

        assert "label" in result
        assert result["label"] in LABEL_NAMES.values()
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert "probabilities" in result
        assert len(result["probabilities"]) == 4

    def test_load_latest(self, tmp_path):
        """load_latest finds the latest model."""
        model_dir = str(tmp_path / "models")
        os.makedirs(model_dir)

        latest_path = os.path.join(model_dir, "latest_model.txt")
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
        model_path = str(tmp_path / "model.txt")
        meta_path = str(tmp_path / "model_meta.json")
        _train_and_save(model_path)

        meta = {"num_features": 22, "timestamp": "20240615"}
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        ranker = RankerModel(model_path, metadata_path=meta_path)
        assert ranker.metadata is not None
        assert ranker.metadata["num_features"] == 22

    def test_save_load_roundtrip(self, tmp_path):
        """Model produces same predictions after save/load."""
        model_path = str(tmp_path / "model.txt")
        original_model = _train_and_save(model_path)

        X = np.random.RandomState(99).randn(10, len(FEATURE_NAMES))
        original_pred = original_model.predict(X)

        ranker = RankerModel(model_path)
        loaded_pred = ranker.predict_proba(X)

        np.testing.assert_allclose(original_pred, loaded_pred, atol=1e-10)
