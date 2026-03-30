"""Tests for the neural predictor model, dataset, and training loop.

Covers: model shapes, gradient flow, save/load, dataset, collate_fn, training loop.
"""
import math
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.prediction.neural_reranker import (
    MAX_SIMILAR_VIDEOS,
    NUM_CANDIDATE_NUMERIC,
    SIMILAR_VIDEO_DIM,
    TITLE_EMBEDDING_DIM,
    NeuralPredictor,
    SimilarVideoEncoder,
    duration_to_bucket,
)
from app.prediction.dataset import (
    CANDIDATE_NUMERIC_KEYS,
    PredictorDataset,
    build_samples_from_db,
    predictor_collate_fn,
)
from app.prediction.models import VideoPredictionResult


# ── Helper fixtures ──────────────────────────────────────────────────


def _make_batch(batch_size=4, n_similar=5):
    """Create a synthetic batch for testing."""
    return {
        "candidate_numeric": torch.randn(batch_size, NUM_CANDIDATE_NUMERIC),
        "title_embedding": torch.randn(batch_size, TITLE_EMBEDDING_DIM),
        "category_id": torch.randint(0, 50, (batch_size,)),
        "duration_bucket": torch.randint(0, 8, (batch_size,)),
        "similar_features": torch.randn(batch_size, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM),
        "similar_padding_mask": torch.ones(batch_size, MAX_SIMILAR_VIDEOS, dtype=torch.bool),
        "target": torch.randn(batch_size) * 2 + 9,  # ~log(views) range
    }


def _make_batch_with_similar(batch_size=4, n_similar=5):
    """Create batch with some non-padded similar videos."""
    batch = _make_batch(batch_size, n_similar)
    for i in range(batch_size):
        batch["similar_padding_mask"][i, :n_similar] = False
    return batch


def _make_sample(channel_id="ch1", target=9.0, n_similar=3):
    """Create a single dataset sample dict."""
    candidate = {k: float(np.random.randn()) for k in CANDIDATE_NUMERIC_KEYS}
    title_emb = np.random.randn(TITLE_EMBEDDING_DIM).tolist()
    similar = [
        {
            "log_views": 8.0 + i,
            "similarity": 0.9 - i * 0.1,
            "rank": float(i + 1),
        }
        for i in range(n_similar)
    ]
    return {
        "candidate": candidate,
        "title_embedding": title_emb,
        "similar_videos": similar,
        "target": target,
        "channel_id": channel_id,
        "category_id": 22,
        "duration_seconds": 600,
    }


# ── Duration Bucket Tests ────────────────────────────────────────────


class TestDurationBucket:
    def test_short(self):
        assert duration_to_bucket(10) == 0

    def test_medium(self):
        assert duration_to_bucket(60) == 1

    def test_standard(self):
        assert duration_to_bucket(200) == 2

    def test_long(self):
        assert duration_to_bucket(400) == 3

    def test_very_long(self):
        assert duration_to_bucket(800) == 4

    def test_hour(self):
        assert duration_to_bucket(2000) == 5

    def test_very_very_long(self):
        assert duration_to_bucket(5000) == 6

    def test_unknown(self):
        assert duration_to_bucket(0) == 7
        assert duration_to_bucket(-1) == 7


# ── SimilarVideoEncoder Tests ────────────────────────────────────────


class TestSimilarVideoEncoder:
    def test_output_shape(self):
        encoder = SimilarVideoEncoder(input_dim=SIMILAR_VIDEO_DIM, hidden_dim=64, num_heads=4)
        features = torch.randn(4, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM)
        mask = torch.ones(4, MAX_SIMILAR_VIDEOS, dtype=torch.bool)
        mask[:, :5] = False  # 5 non-padded

        out = encoder(features, mask)
        assert out.shape == (4, 64)

    def test_all_padded_no_crash(self):
        encoder = SimilarVideoEncoder(input_dim=SIMILAR_VIDEO_DIM, hidden_dim=64, num_heads=4)
        features = torch.zeros(2, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM)
        mask = torch.ones(2, MAX_SIMILAR_VIDEOS, dtype=torch.bool)  # all padded

        out = encoder(features, mask)
        assert out.shape == (2, 64)

    def test_gradient_flows(self):
        encoder = SimilarVideoEncoder(input_dim=SIMILAR_VIDEO_DIM)
        features = torch.randn(2, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM, requires_grad=True)
        mask = torch.ones(2, MAX_SIMILAR_VIDEOS, dtype=torch.bool)
        mask[:, :3] = False

        out = encoder(features, mask)
        loss = out.sum()
        loss.backward()
        assert features.grad is not None


# ── NeuralPredictor Model Tests ───────────────────────────────────────


class TestNeuralPredictor:
    def test_output_shape(self):
        model = NeuralPredictor()
        batch = _make_batch_with_similar(batch_size=4, n_similar=5)
        out = model(
            batch["candidate_numeric"],
            batch["title_embedding"],
            batch["category_id"],
            batch["duration_bucket"],
            batch["similar_features"],
            batch["similar_padding_mask"],
        )
        assert out.shape == (4, 1)

    def test_single_sample(self):
        model = NeuralPredictor()
        batch = _make_batch_with_similar(batch_size=1, n_similar=3)
        out = model(
            batch["candidate_numeric"],
            batch["title_embedding"],
            batch["category_id"],
            batch["duration_bucket"],
            batch["similar_features"],
            batch["similar_padding_mask"],
        )
        assert out.shape == (1, 1)
        assert torch.isfinite(out).all()

    def test_gradient_flow(self):
        model = NeuralPredictor()
        batch = _make_batch_with_similar(batch_size=4)

        out = model(
            batch["candidate_numeric"],
            batch["title_embedding"],
            batch["category_id"],
            batch["duration_bucket"],
            batch["similar_features"],
            batch["similar_padding_mask"],
        )
        loss = out.sum()
        loss.backward()

        # Check that all trainable params have gradients
        for name, param in model.named_parameters():
            if param.requires_grad:
                assert param.grad is not None, f"No gradient for {name}"

    def test_no_similar_videos(self):
        """Model should still work with all-padded similar videos."""
        model = NeuralPredictor()
        batch = _make_batch(batch_size=2)
        # All similar videos are padded (mask is all True by default)

        out = model(
            batch["candidate_numeric"],
            batch["title_embedding"],
            batch["category_id"],
            batch["duration_bucket"],
            batch["similar_features"],
            batch["similar_padding_mask"],
        )
        assert out.shape == (2, 1)
        assert torch.isfinite(out).all()

    def test_save_load(self):
        model = NeuralPredictor(candidate_hidden=64, similar_hidden=32)
        model.eval()  # Disable dropout for deterministic comparison

        # Get a prediction before save
        batch = _make_batch_with_similar(batch_size=2)
        with torch.no_grad():
            pred_before = model(
                batch["candidate_numeric"], batch["title_embedding"],
                batch["category_id"], batch["duration_bucket"],
                batch["similar_features"], batch["similar_padding_mask"],
            )

        # Save and load
        with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
            path = f.name
        try:
            model.save(path)
            loaded = NeuralPredictor.load(path)
            loaded.eval()

            with torch.no_grad():
                pred_after = loaded(
                    batch["candidate_numeric"], batch["title_embedding"],
                    batch["category_id"], batch["duration_bucket"],
                    batch["similar_features"], batch["similar_padding_mask"],
                )

            assert torch.allclose(pred_before, pred_after, atol=1e-5)
        finally:
            os.unlink(path)

    def test_eval_mode(self):
        model = NeuralPredictor()
        model.eval()
        batch = _make_batch_with_similar(batch_size=2)

        with torch.no_grad():
            out = model(
                batch["candidate_numeric"], batch["title_embedding"],
                batch["category_id"], batch["duration_bucket"],
                batch["similar_features"], batch["similar_padding_mask"],
            )
        assert out.shape == (2, 1)

    def test_different_hidden_dims(self):
        model = NeuralPredictor(candidate_hidden=256, similar_hidden=128, num_heads=8)
        batch = _make_batch_with_similar(batch_size=2)
        out = model(
            batch["candidate_numeric"], batch["title_embedding"],
            batch["category_id"], batch["duration_bucket"],
            batch["similar_features"], batch["similar_padding_mask"],
        )
        assert out.shape == (2, 1)


# ── PredictorDataset Tests ────────────────────────────────────────────


class TestPredictorDataset:
    def test_length(self):
        samples = [_make_sample() for _ in range(10)]
        ds = PredictorDataset(samples)
        assert len(ds) == 10

    def test_getitem(self):
        samples = [_make_sample(target=9.5, n_similar=5)]
        ds = PredictorDataset(samples)
        item = ds[0]

        assert item["candidate_numeric"].shape == (NUM_CANDIDATE_NUMERIC,)
        assert item["title_embedding"].shape == (TITLE_EMBEDDING_DIM,)
        assert isinstance(item["category_id"], int)
        assert isinstance(item["duration_bucket"], int)
        assert isinstance(item["similar_videos"], list)
        assert len(item["similar_videos"]) == 5
        assert item["target"] == 9.5

    def test_no_title_embedding(self):
        """Dataset should handle missing title embedding gracefully."""
        sample = _make_sample()
        sample["title_embedding"] = None
        ds = PredictorDataset([sample])
        item = ds[0]
        assert item["title_embedding"].shape == (TITLE_EMBEDDING_DIM,)
        assert torch.all(item["title_embedding"] == 0)

    def test_no_similar_videos(self):
        sample = _make_sample(n_similar=0)
        ds = PredictorDataset([sample])
        item = ds[0]
        assert item["similar_videos"] == []

    def test_category_clamped(self):
        sample = _make_sample()
        sample["category_id"] = 999  # out of range
        ds = PredictorDataset([sample])
        item = ds[0]
        assert 0 <= item["category_id"] < 50


# ── Collate Function Tests ───────────────────────────────────────────


class TestCollateFn:
    def test_basic_collate(self):
        samples = [_make_sample(n_similar=3), _make_sample(n_similar=5)]
        ds = PredictorDataset(samples)
        batch = predictor_collate_fn([ds[0], ds[1]])

        assert batch["candidate_numeric"].shape == (2, NUM_CANDIDATE_NUMERIC)
        assert batch["title_embedding"].shape == (2, TITLE_EMBEDDING_DIM)
        assert batch["category_id"].shape == (2,)
        assert batch["duration_bucket"].shape == (2,)
        assert batch["similar_features"].shape == (2, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM)
        assert batch["similar_padding_mask"].shape == (2, MAX_SIMILAR_VIDEOS)
        assert batch["target"].shape == (2,)

    def test_padding_mask_correct(self):
        samples = [_make_sample(n_similar=3), _make_sample(n_similar=0)]
        ds = PredictorDataset(samples)
        batch = predictor_collate_fn([ds[0], ds[1]])

        # First sample: 3 non-padded, rest padded
        assert batch["similar_padding_mask"][0, :3].sum() == 0  # not padded
        assert batch["similar_padding_mask"][0, 3:].all()  # padded

        # Second sample: all padded
        assert batch["similar_padding_mask"][1].all()

    def test_variable_length_similar(self):
        """Samples with different numbers of similar videos should collate correctly."""
        samples = [
            _make_sample(n_similar=1),
            _make_sample(n_similar=10),
            _make_sample(n_similar=20),
        ]
        ds = PredictorDataset(samples)
        batch = predictor_collate_fn([ds[i] for i in range(3)])

        assert batch["similar_features"].shape == (3, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM)
        # Check non-padded positions
        assert not batch["similar_padding_mask"][0, 0]
        assert batch["similar_padding_mask"][0, 1]  # only 1 similar for sample 0
        assert not batch["similar_padding_mask"][1, 9]
        assert batch["similar_padding_mask"][1, 10]

    def test_collated_batch_through_model(self):
        """Collated batch should work with the model."""
        samples = [_make_sample(n_similar=i) for i in range(5)]
        ds = PredictorDataset(samples)
        batch = predictor_collate_fn([ds[i] for i in range(5)])

        model = NeuralPredictor()
        out = model(
            batch["candidate_numeric"],
            batch["title_embedding"],
            batch["category_id"],
            batch["duration_bucket"],
            batch["similar_features"],
            batch["similar_padding_mask"],
        )
        assert out.shape == (5, 1)


# ── Training Integration Test ────────────────────────────────────────


class TestTrainingLoop:
    def test_mini_training_loop(self):
        """Test that a mini training loop converges on synthetic data."""
        torch.manual_seed(42)

        model = NeuralPredictor(candidate_hidden=32, similar_hidden=16, num_heads=2)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-2)
        criterion = torch.nn.MSELoss()

        # Create synthetic dataset
        samples = [_make_sample(target=9.0 + np.random.randn() * 0.5, n_similar=5) for _ in range(32)]
        ds = PredictorDataset(samples)
        loader = torch.utils.data.DataLoader(
            ds, batch_size=8, shuffle=True, collate_fn=predictor_collate_fn,
        )

        losses = []
        for epoch in range(10):
            model.train()
            epoch_loss = 0
            for batch in loader:
                optimizer.zero_grad()
                preds = model(
                    batch["candidate_numeric"], batch["title_embedding"],
                    batch["category_id"], batch["duration_bucket"],
                    batch["similar_features"], batch["similar_padding_mask"],
                ).squeeze(-1)
                loss = criterion(preds, batch["target"])
                loss.backward()
                optimizer.step()
                epoch_loss += loss.item()
            losses.append(epoch_loss)

        # Loss should decrease
        assert losses[-1] < losses[0], f"Loss did not decrease: {losses[0]:.4f} -> {losses[-1]:.4f}"

    def test_eval_predictions(self):
        """Test that model produces reasonable predictions in eval mode."""
        model = NeuralPredictor(candidate_hidden=32, similar_hidden=16)
        model.eval()

        samples = [_make_sample(target=9.0, n_similar=5) for _ in range(8)]
        ds = PredictorDataset(samples)
        batch = predictor_collate_fn([ds[i] for i in range(8)])

        with torch.no_grad():
            preds = model(
                batch["candidate_numeric"], batch["title_embedding"],
                batch["category_id"], batch["duration_bucket"],
                batch["similar_features"], batch["similar_padding_mask"],
            )

        assert preds.shape == (8, 1)
        assert torch.isfinite(preds).all()


# ── build_samples_from_db Tests ──────────────────────────────────────


class TestBuildSamplesFromDB:
    def test_builds_samples(self):
        # Mock CompetitorVideo
        videos = []
        for i in range(3):
            v = MagicMock()
            v.bilibili_uid = f"ch{i}"
            v.duration = 600
            videos.append(v)

        targets = np.array([8.0, 9.0, 10.0])
        feature_dicts = [
            {k: 0.0 for k in CANDIDATE_NUMERIC_KEYS + ["yt_category_id", "duration"]}
            for _ in range(3)
        ]

        samples = build_samples_from_db(videos, targets, feature_dicts)
        assert len(samples) == 3
        assert samples[0]["target"] == 8.0
        assert samples[0]["channel_id"] == "ch0"
        assert "candidate" in samples[0]
        assert samples[0]["title_embedding"] is None  # no embeddings provided

    def test_builds_with_similar_videos(self):
        videos = [MagicMock(bilibili_uid="ch1", duration=300)]
        targets = np.array([9.5])
        feature_dicts = [{k: 0.0 for k in CANDIDATE_NUMERIC_KEYS + ["yt_category_id", "duration"]}]
        similar = [[{"log_views": 8.0, "similarity": 0.9, "rank": 1}]]

        samples = build_samples_from_db(videos, targets, feature_dicts, similar_videos_list=similar)
        assert len(samples[0]["similar_videos"]) == 1

    def test_builds_with_title_embeddings(self):
        videos = [MagicMock(bilibili_uid="ch1", duration=300)]
        targets = np.array([9.5])
        feature_dicts = [{k: 0.0 for k in CANDIDATE_NUMERIC_KEYS + ["yt_category_id", "duration"]}]
        embeddings = np.random.randn(1, TITLE_EMBEDDING_DIM).astype(np.float32)

        samples = build_samples_from_db(
            videos, targets, feature_dicts, title_embeddings=embeddings,
        )
        assert samples[0]["title_embedding"] is not None
        assert len(samples[0]["title_embedding"]) == TITLE_EMBEDDING_DIM
