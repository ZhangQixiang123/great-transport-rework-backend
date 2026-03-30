"""Tests for VideoTitleDataset and DataLoader."""
import math
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest
import torch

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


class MockTokenizer:
    """Mock tokenizer that returns fixed-size tensors."""

    def __call__(self, text, max_length=128, padding="max_length",
                 truncation=True, return_tensors="pt"):
        seq_len = max_length
        return {
            "input_ids": torch.ones(1, seq_len, dtype=torch.long),
            "attention_mask": torch.ones(1, seq_len, dtype=torch.long),
        }


@pytest.fixture
def mock_tokenizer():
    with patch(
        "app.embeddings.dataset.AutoTokenizer.from_pretrained",
        return_value=MockTokenizer(),
    ):
        yield


class TestVideoTitleDataset:
    def test_length(self, mock_tokenizer):
        from app.embeddings.dataset import VideoTitleDataset
        videos = [_make_video(bvid=f"BV{i}") for i in range(5)]
        ds = VideoTitleDataset(videos)
        assert len(ds) == 5

    def test_getitem_keys(self, mock_tokenizer):
        from app.embeddings.dataset import VideoTitleDataset
        ds = VideoTitleDataset([_make_video()])
        item = ds[0]
        assert "input_ids" in item
        assert "attention_mask" in item
        assert "target" in item

    def test_getitem_shapes(self, mock_tokenizer):
        from app.embeddings.dataset import VideoTitleDataset
        ds = VideoTitleDataset([_make_video()], max_length=64)
        item = ds[0]
        assert item["input_ids"].shape == (64,)
        assert item["attention_mask"].shape == (64,)
        assert item["target"].shape == ()

    def test_target_value(self, mock_tokenizer):
        from app.embeddings.dataset import VideoTitleDataset
        video = _make_video(views=10000)
        ds = VideoTitleDataset([video])
        item = ds[0]
        expected = math.log1p(10000)
        assert item["target"].item() == pytest.approx(expected, rel=1e-5)

    def test_zero_views_target(self, mock_tokenizer):
        from app.embeddings.dataset import VideoTitleDataset
        video = _make_video(views=0)
        ds = VideoTitleDataset([video])
        item = ds[0]
        assert item["target"].item() == 0.0


class TestCreateDataloaders:
    def test_creates_loaders(self, mock_tokenizer):
        from app.embeddings.dataset import create_dataloaders
        videos = [_make_video(bvid=f"BV{i}", views=i * 100 + 1) for i in range(10)]
        train_loader, val_loader = create_dataloaders(
            videos, train_idx=list(range(7)), val_idx=list(range(7, 10)),
            batch_size=4,
        )
        # Check batch from train loader
        batch = next(iter(train_loader))
        assert batch["input_ids"].shape[0] <= 4
        assert batch["target"].shape[0] <= 4

    def test_val_loader_not_shuffled(self, mock_tokenizer):
        from app.embeddings.dataset import create_dataloaders
        videos = [_make_video(bvid=f"BV{i}", views=i * 100 + 1) for i in range(10)]
        _, val_loader = create_dataloaders(
            videos, train_idx=list(range(7)), val_idx=list(range(7, 10)),
            batch_size=10,
        )
        batch = next(iter(val_loader))
        assert batch["target"].shape[0] == 3
