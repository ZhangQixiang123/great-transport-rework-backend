"""Tests for the GPBoost training pipeline."""
import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import gpboost as gpb
import numpy as np
import pytest

from app.db.database import CompetitorVideo, Database
from app.training.trainer import train_model
from app.training.features import FEATURE_NAMES


# 6 distinct channels for GroupKFold (need >= n_cv_folds groups)
CHANNEL_UIDS = ["ch_A", "ch_B", "ch_C", "ch_D", "ch_E", "ch_F"]


def _make_videos(n=200, seed=42):
    """Generate synthetic CompetitorVideo list for training.

    Assigns videos to 6 distinct channels to support GroupKFold.
    """
    rng = np.random.RandomState(seed)
    videos = []
    for i in range(n):
        uid = CHANNEL_UIDS[i % len(CHANNEL_UIDS)]
        has_yt = rng.random() > 0.5
        base_views = 1000 + rng.randint(0, 50000)
        if has_yt:
            base_views *= 2
        videos.append(CompetitorVideo(
            bvid=f"BV{i:05d}",
            bilibili_uid=uid,
            title=f"Test Video {i}" + (" 123" if rng.random() > 0.5 else ""),
            description="Description " * rng.randint(1, 20),
            duration=rng.randint(60, 3600),
            views=base_views,
            likes=base_views // 20 + rng.randint(0, 100),
            coins=base_views // 40 + rng.randint(0, 50),
            favorites=base_views // 30 + rng.randint(0, 80),
            shares=rng.randint(0, 50),
            danmaku=rng.randint(0, 100),
            comments=rng.randint(0, 30),
            publish_time=datetime(2024, 1, 1 + i % 28, rng.randint(0, 23), 0),
            collected_at=datetime(2024, 6, 15, 10, 0),
            youtube_source_id="yt123" if has_yt else None,
            label=None,
        ))
    return videos


def _mock_db(videos):
    """Create a mock Database that returns given videos for regression."""
    db = MagicMock(spec=Database)
    db.connection_string = ":memory:"

    mock_conn = MagicMock()
    rows = []
    for v in videos:
        row = MagicMock()
        row.__getitem__ = lambda self, key, v=v: {
            "bvid": v.bvid, "bilibili_uid": v.bilibili_uid,
            "title": v.title, "description": v.description,
            "duration": v.duration, "views": v.views,
            "likes": v.likes, "coins": v.coins,
            "favorites": v.favorites, "shares": v.shares,
            "danmaku": v.danmaku, "comments": v.comments,
            "publish_time": v.publish_time.isoformat() if v.publish_time else None,
            "collected_at": v.collected_at.isoformat(),
            "youtube_source_id": v.youtube_source_id,
            "label": v.label,
        }[key]
        rows.append(row)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn.execute.return_value = mock_cursor
    db._conn = mock_conn

    return db


class TestTrainModel:
    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_synthetic_training(self, mock_yt, tmp_path):
        """Train on synthetic data and verify outputs."""
        videos = _make_videos(200)
        db = _mock_db(videos)
        model_dir = str(tmp_path / "models")

        model, report, metadata = train_model(
            db,
            model_dir=model_dir,
            num_rounds=20,
            cv_rounds=20,
            min_samples=50,
        )

        assert model is not None
        assert report is not None
        assert report.rmse > 0
        assert -10.0 <= report.r2 <= 1.0
        assert report.mae > 0

    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_model_files_saved(self, mock_yt, tmp_path):
        """Verify model and metadata files are created."""
        videos = _make_videos(100)
        db = _mock_db(videos)
        model_dir = str(tmp_path / "models")

        train_model(db, model_dir=model_dir, num_rounds=10, cv_rounds=10)

        assert os.path.exists(os.path.join(model_dir, "latest_model.json"))
        assert os.path.exists(os.path.join(model_dir, "latest_model_meta.json"))

        with open(os.path.join(model_dir, "latest_model_meta.json")) as f:
            meta = json.load(f)
        assert meta["model_type"] == "gpboost_mixed_effects"
        assert "feature_names" in meta
        assert meta["num_features"] == len(FEATURE_NAMES)
        assert "percentile_thresholds" in meta
        assert "evaluation" in meta
        assert "cv_evaluation" in meta

    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_cv_evaluation_in_metadata(self, mock_yt, tmp_path):
        """CV evaluation metrics are saved in metadata."""
        videos = _make_videos(200)
        db = _mock_db(videos)
        model_dir = str(tmp_path / "models")

        _, _, metadata = train_model(
            db, model_dir=model_dir, num_rounds=10, cv_rounds=10,
            n_cv_folds=5,
        )

        cv = metadata["cv_evaluation"]
        assert "mean_rmse" in cv
        assert "mean_r2" in cv
        assert "n_folds" in cv
        assert cv["n_folds"] >= 2
        assert len(cv["per_fold"]) == cv["n_folds"]

    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_insufficient_data_returns_none(self, mock_yt, tmp_path):
        """When data is insufficient, model and report are None."""
        videos = _make_videos(10)
        db = _mock_db(videos)

        model, report, metadata = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
            min_samples=50,
        )

        assert model is None
        assert report is None
        assert "error" in metadata

    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_no_data_at_all(self, mock_yt, tmp_path):
        """Empty database returns graceful failure."""
        db = _mock_db([])

        model, report, metadata = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
        )

        assert model is None
        assert report is None

    @patch("app.training.features._load_youtube_stats_map", return_value={})
    def test_custom_learning_rate(self, mock_yt, tmp_path):
        """Custom learning rate is accepted."""
        videos = _make_videos(100)
        db = _mock_db(videos)

        model, report, _ = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
            cv_rounds=10,
            learning_rate=0.1,
        )

        assert model is not None
