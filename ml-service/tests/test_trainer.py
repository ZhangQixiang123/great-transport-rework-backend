"""Tests for the LightGBM training pipeline."""
import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import lightgbm as lgb
import numpy as np
import pytest

from app.db.database import CompetitorVideo, Database
from app.training.trainer import train_model
from app.training.features import FEATURE_NAMES


def _make_videos(n=200, seed=42):
    """Generate synthetic CompetitorVideo list for training."""
    rng = np.random.RandomState(seed)
    videos = []
    for i in range(n):
        # Views correlated with duration and YouTube source for learnable signal
        has_yt = rng.random() > 0.5
        base_views = 1000 + rng.randint(0, 50000)
        if has_yt:
            base_views *= 2  # YouTube-sourced videos tend to do better
        videos.append(CompetitorVideo(
            bvid=f"BV{i:05d}",
            bilibili_uid="12345",
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
            label=None,  # regression doesn't need labels
        ))
    return videos


def _mock_db(videos):
    """Create a mock Database that returns given videos for regression."""
    db = MagicMock(spec=Database)
    db.connection_string = ":memory:"

    # Mock the _conn for load_regression_data direct SQL access
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
    def test_synthetic_training(self, tmp_path):
        """Train on synthetic data and verify outputs."""
        videos = _make_videos(200)
        db = _mock_db(videos)
        model_dir = str(tmp_path / "models")

        model, report, metadata = train_model(
            db,
            model_dir=model_dir,
            num_rounds=20,
            use_gpu=False,
            min_samples=50,
        )

        assert model is not None
        assert report is not None
        assert report.rmse > 0
        assert -1.0 <= report.r2 <= 1.0
        assert report.mae > 0

    def test_model_files_saved(self, tmp_path):
        """Verify model and metadata files are created."""
        videos = _make_videos(100)
        db = _mock_db(videos)
        model_dir = str(tmp_path / "models")

        train_model(db, model_dir=model_dir, num_rounds=10, use_gpu=False)

        assert os.path.exists(os.path.join(model_dir, "latest_model.txt"))
        assert os.path.exists(os.path.join(model_dir, "latest_model_meta.json"))

        with open(os.path.join(model_dir, "latest_model_meta.json")) as f:
            meta = json.load(f)
        assert meta["model_type"] == "regression"
        assert "feature_names" in meta
        assert meta["num_features"] == len(FEATURE_NAMES)
        assert "percentile_thresholds" in meta
        assert "evaluation" in meta

    def test_insufficient_data_returns_none(self, tmp_path):
        """When data is insufficient, model and report are None."""
        videos = _make_videos(10)
        db = _mock_db(videos)

        model, report, metadata = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
            use_gpu=False,
            min_samples=50,
        )

        assert model is None
        assert report is None
        assert "error" in metadata

    def test_no_data_at_all(self, tmp_path):
        """Empty database returns graceful failure."""
        db = _mock_db([])

        model, report, metadata = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
            use_gpu=False,
        )

        assert model is None
        assert report is None

    def test_custom_learning_rate(self, tmp_path):
        """Custom learning rate is accepted."""
        videos = _make_videos(100)
        db = _mock_db(videos)

        model, report, _ = train_model(
            db,
            model_dir=str(tmp_path / "models"),
            num_rounds=10,
            learning_rate=0.1,
            use_gpu=False,
        )

        assert model is not None
