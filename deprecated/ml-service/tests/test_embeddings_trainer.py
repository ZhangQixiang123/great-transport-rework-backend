"""Tests for embedding fine-tuning trainer."""
import math
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock

import numpy as np
import pytest
import torch
import torch.nn as nn

from app.db.database import CompetitorVideo


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


def _make_mock_backbone():
    """Create a mock transformer backbone."""
    class MockOutput:
        def __init__(self, batch_size, seq_len, hidden_dim=384, device="cpu"):
            self.last_hidden_state = torch.randn(batch_size, seq_len, hidden_dim, device=device)

    class MockBackbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.dummy = nn.Linear(1, 1)

        def forward(self, input_ids=None, attention_mask=None):
            batch_size = input_ids.shape[0]
            seq_len = input_ids.shape[1]
            return MockOutput(batch_size, seq_len, device=input_ids.device)

    return MockBackbone()


class MockTokenizer:
    def __call__(self, text_or_texts, max_length=128, padding=True,
                 truncation=True, return_tensors="pt"):
        if isinstance(text_or_texts, str):
            text_or_texts = [text_or_texts]
        batch_size = len(text_or_texts)
        seq_len = max_length if isinstance(max_length, int) else 128
        return {
            "input_ids": torch.ones(batch_size, seq_len, dtype=torch.long),
            "attention_mask": torch.ones(batch_size, seq_len, dtype=torch.long),
        }


@pytest.fixture
def mock_ml_deps():
    """Mock transformer backbone and tokenizer."""
    with patch(
        "app.embeddings.model.AutoModel.from_pretrained",
        return_value=_make_mock_backbone(),
    ), patch(
        "app.embeddings.model.AutoTokenizer.from_pretrained",
        return_value=MockTokenizer(),
    ), patch(
        "app.embeddings.dataset.AutoTokenizer.from_pretrained",
        return_value=MockTokenizer(),
    ):
        yield


def _make_mock_db(n_videos=150, n_channels=5):
    """Create a mock database with videos."""
    db = MagicMock()
    db._conn = MagicMock()
    db.connection_string = ":memory:"

    videos = []
    for i in range(n_videos):
        ch = f"ch{i % n_channels}"
        v = _make_video(
            bvid=f"BV{i:04d}",
            bilibili_uid=ch,
            title=f"Video title number {i}",
            views=max(1, int(np.random.lognormal(8, 2))),
        )
        videos.append(v)

    targets = np.array([math.log1p(v.views) for v in videos])

    return db, videos, targets


class TestFineTuneEmbeddings:
    def test_training_completes(self, mock_ml_deps, tmp_path):
        from app.embeddings.trainer import fine_tune_embeddings

        db, videos, targets = _make_mock_db(n_videos=150, n_channels=5)

        with patch(
            "app.embeddings.trainer.load_regression_data",
            return_value=(videos, targets, {}),
        ):
            embedder, metrics = fine_tune_embeddings(
                db,
                model_dir=str(tmp_path),
                epochs=3,
                batch_size=32,
                patience=2,
            )

        assert embedder is not None
        assert metrics is not None
        assert metrics["best_epoch"] >= 1
        assert metrics["num_videos"] == 150
        assert metrics["num_channels"] == 5
        assert metrics["vector_store_size"] == 150

        # Check files were saved
        import os
        assert os.path.exists(tmp_path / "embedder.pt")
        assert os.path.exists(tmp_path / "vector_store.npz")

    def test_full_finetune_differential_lr(self, mock_ml_deps, tmp_path):
        """Full fine-tune uses separate LRs for backbone and heads."""
        from app.embeddings.trainer import fine_tune_embeddings

        db, videos, targets = _make_mock_db(n_videos=150, n_channels=5)

        with patch(
            "app.embeddings.trainer.load_regression_data",
            return_value=(videos, targets, {}),
        ):
            embedder, metrics = fine_tune_embeddings(
                db,
                model_dir=str(tmp_path),
                epochs=2,
                batch_size=32,
                patience=2,
                freeze_backbone=False,
                backbone_lr=2e-5,
                lr=1e-3,
            )

        assert embedder is not None
        assert metrics is not None
        assert metrics["freeze_backbone"] is False

    def test_early_stopping(self, mock_ml_deps, tmp_path):
        from app.embeddings.trainer import fine_tune_embeddings

        db, videos, targets = _make_mock_db(n_videos=150, n_channels=5)

        with patch(
            "app.embeddings.trainer.load_regression_data",
            return_value=(videos, targets, {}),
        ):
            embedder, metrics = fine_tune_embeddings(
                db,
                model_dir=str(tmp_path),
                epochs=100,
                batch_size=32,
                patience=2,
            )

        # Should stop well before 100 epochs
        assert metrics["total_epochs"] < 100

    def test_insufficient_data_returns_none(self, mock_ml_deps, tmp_path):
        from app.embeddings.trainer import fine_tune_embeddings

        db, videos, targets = _make_mock_db(n_videos=50, n_channels=2)
        videos = videos[:50]
        targets = targets[:50]

        with patch(
            "app.embeddings.trainer.load_regression_data",
            return_value=(videos, targets, {}),
        ):
            embedder, metrics = fine_tune_embeddings(
                db,
                model_dir=str(tmp_path),
                epochs=3,
            )

        assert embedder is None
        assert metrics is None


class TestRegressionHead:
    def test_output_shape(self):
        from app.embeddings.trainer import RegressionHead
        head = RegressionHead(input_dim=128)
        x = torch.randn(4, 128)
        out = head(x)
        assert out.shape == (4,)

    def test_custom_dim(self):
        from app.embeddings.trainer import RegressionHead
        head = RegressionHead(input_dim=64)
        x = torch.randn(2, 64)
        out = head(x)
        assert out.shape == (2,)
