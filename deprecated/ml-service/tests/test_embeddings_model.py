"""Tests for TitleEmbedder nn.Module."""
import os
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
import torch
import torch.nn as nn


def _make_mock_backbone():
    """Create a mock transformer backbone that returns fixed hidden states."""
    class MockOutput:
        def __init__(self, batch_size, seq_len, hidden_dim=384):
            self.last_hidden_state = torch.randn(batch_size, seq_len, hidden_dim)

    class MockBackbone(nn.Module):
        def __init__(self):
            super().__init__()
            self.dummy = nn.Linear(1, 1)  # needs at least one param

        def forward(self, input_ids=None, attention_mask=None):
            batch_size = input_ids.shape[0]
            seq_len = input_ids.shape[1]
            return MockOutput(batch_size, seq_len)

    return MockBackbone()


@pytest.fixture
def mock_backbone():
    with patch(
        "app.embeddings.model.AutoModel.from_pretrained",
        return_value=_make_mock_backbone(),
    ):
        yield


@pytest.fixture
def mock_tokenizer_for_encode():
    """Mock tokenizer for the encode() method."""
    class MockTok:
        def __call__(self, texts, max_length=128, padding=True,
                     truncation=True, return_tensors="pt"):
            batch_size = len(texts)
            return {
                "input_ids": torch.ones(batch_size, 10, dtype=torch.long),
                "attention_mask": torch.ones(batch_size, 10, dtype=torch.long),
            }

    with patch(
        "app.embeddings.model.AutoTokenizer.from_pretrained",
        return_value=MockTok(),
    ):
        yield


class TestTitleEmbedderForward:
    def test_output_shape(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=128, freeze_backbone=True)
        input_ids = torch.ones(4, 10, dtype=torch.long)
        attention_mask = torch.ones(4, 10, dtype=torch.long)
        out = model(input_ids, attention_mask)
        assert out.shape == (4, 128)

    def test_custom_projection_dim(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=64, freeze_backbone=True)
        input_ids = torch.ones(2, 10, dtype=torch.long)
        attention_mask = torch.ones(2, 10, dtype=torch.long)
        out = model(input_ids, attention_mask)
        assert out.shape == (2, 64)

    def test_frozen_backbone_no_grad(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(freeze_backbone=True)
        for param in model.backbone.parameters():
            assert not param.requires_grad

    def test_unfrozen_backbone_has_grad(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(freeze_backbone=False)
        for param in model.backbone.parameters():
            assert param.requires_grad

    def test_projection_has_grad(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(freeze_backbone=True)
        for param in model.projection.parameters():
            assert param.requires_grad


class TestTitleEmbedderEncode:
    def test_encode_returns_numpy(self, mock_backbone, mock_tokenizer_for_encode):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=128, freeze_backbone=True)
        result = model.encode(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 128)

    def test_encode_single(self, mock_backbone, mock_tokenizer_for_encode):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=64, freeze_backbone=True)
        result = model.encode(["single title"])
        assert result.shape == (1, 64)


class TestTitleEmbedderSaveLoad:
    def test_save_load_roundtrip(self, mock_backbone, tmp_path):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=64, freeze_backbone=True)
        save_path = str(tmp_path / "test_embedder.pt")
        model.save(save_path)
        assert os.path.exists(save_path)

        loaded = TitleEmbedder.load(save_path)
        assert loaded.projection_dim == 64
        assert loaded.freeze_backbone is True

    def test_config(self, mock_backbone):
        from app.embeddings.model import TitleEmbedder
        model = TitleEmbedder(projection_dim=64, freeze_backbone=False)
        config = model.get_config()
        assert config["projection_dim"] == 64
        assert config["freeze_backbone"] is False
